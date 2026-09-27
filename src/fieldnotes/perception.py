"""Optional local models. Network access is restricted to explicit setup()."""
from __future__ import annotations

import heapq
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

CLIP_REPO = 'apple/MobileCLIP2-S0'
CLIP_REVISION = '3136ea51c8ed56b9f9abfab04cb816735aaad6cb'
EDGE_REPO = 'facebook/EdgeTAM'
EDGE_REVISION = '14d7ecc48c656b94e5184519f698cd5386c5a2bf'
EDGE_CODE_REVISION = '7711e012a30a2402c4eaab637bdb00a521302c91'
EMBED_VERSION = f'MobileCLIP2-S0:{CLIP_REVISION}:openclip-3.3.0:rgb-mean0-std1-v1'


def setup(root: Path):
    os.environ.pop("HF_HUB_OFFLINE", None)
    from huggingface_hub import hf_hub_download
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'EdgeTAM'
    if not source.exists():
        subprocess.run(['git','clone','https://github.com/facebookresearch/EdgeTAM.git',str(source)],check=True)
    head=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if head != EDGE_CODE_REVISION:
        if subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip():
            raise ValueError('EdgeTAM source is modified; refusing to overwrite')
        subprocess.run(['git','-C',str(source),'checkout','--detach',EDGE_CODE_REVISION],check=True)
    assets=[]
    for repo,revision,name,destination in [(CLIP_REPO,CLIP_REVISION,'mobileclip2_s0.pt','mobileclip2_s0.pt'),
                                         (CLIP_REPO,CLIP_REVISION,'LICENSE','MobileCLIP2-LICENSE'),
                                         (EDGE_REPO,EDGE_REVISION,'edgetam.pt','edgetam.pt')]:
        path=hf_hub_download(repo_id=repo,revision=revision,filename=name)
        target=root/destination
        if not target.exists(): shutil.copyfile(path,target)
        digest=hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != hashlib.sha256(Path(path).read_bytes()).hexdigest():
            raise ValueError(f'Model asset differs from pinned upstream: {target}')
        assets.append({'repo':repo,'revision':revision,'filename':destination,'sha256':digest})
    manifest={'edge_code_revision':EDGE_CODE_REVISION,'assets':assets}
    (root/'models.json').write_text(json.dumps(manifest,indent=2))
    return manifest


class Models:
    def __init__(self, root: Path):
        self.root=root.resolve(); self.lock=threading.RLock()
        self.clip=None; self.predictor=None
        self.clip_error=None; self.edge_error=None; self.fallbacks=[]
        self.device=None; self.clip_device=None; self.edge_device=None
        os.environ.setdefault("TQDM_DISABLE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    def status(self):
        return {'mobileclip':{'model':CLIP_REPO,'revision':CLIP_REVISION,
                    'weights_present':(self.root/'mobileclip2_s0.pt').exists(),'loaded':self.clip is not None,'error':self.clip_error},
                'edgetam':{'model':EDGE_REPO,'revision':EDGE_REVISION,'code_revision':EDGE_CODE_REVISION,
                    'weights_present':(self.root/'edgetam.pt').exists(),'loaded':self.predictor is not None,'error':self.edge_error,
                    'configured_device':os.getenv('FIELDNOTES_TRACK_DEVICE','cpu'),
                    'device_policy':'CPU by default; MPS is experimental for EdgeTAM'},
                'device':self.device,'clip_device':self.clip_device,'edge_device':self.edge_device,'fallbacks':self.fallbacks}

    def choose_device(self):
        import torch
        torch.set_num_threads(2)
        requested=os.getenv('FIELDNOTES_MODEL_DEVICE','auto')
        if requested not in ('auto','mps','cpu'): raise ValueError('Model device must be auto, mps or cpu')
        self.device='mps' if requested!='cpu' and torch.backends.mps.is_available() else 'cpu'
        return self.device

    def load_clip(self):
        if self.clip is not None: return
        if not (self.root/'mobileclip2_s0.pt').is_file():
            raise ValueError('MobileCLIP2 weights missing. Run fieldnotes setup-memory-models.')
        import open_clip
        self.choose_device()
        self.clip,_,self.preprocess=open_clip.create_model_and_transforms('MobileCLIP2-S0',
            pretrained=str(self.root/'mobileclip2_s0.pt'),device=self.device,image_mean=(0,0,0),image_std=(1,1,1))
        self.clip_device=self.device
        self.clip.eval(); self.tokenizer=open_clip.get_tokenizer('MobileCLIP2-S0')

    def embedding(self, *, image: Path | None = None, text: str | None = None):
        with self.lock:
            try:
                import torch
                from PIL import Image
                self.load_clip()
                data=self.preprocess(Image.open(image).convert('RGB')).unsqueeze(0) if image else self.tokenizer([text])
                def encode():
                    with torch.inference_mode():
                        value=self.clip.encode_image(data.to(self.clip_device)) if image else self.clip.encode_text(data.to(self.clip_device))
                        value=value.float(); value=value/value.norm(dim=-1,keepdim=True).clamp_min(1e-8)
                        return value[0].cpu().numpy()
                try: result=encode()
                except (RuntimeError,NotImplementedError) as exc:
                    if self.clip_device!='mps': raise
                    self.fallbacks.append('MobileCLIP2 CPU fallback: '+str(exc)[:200])
                    self.device='cpu'; self.clip_device='cpu'; self.clip.to('cpu'); result=encode()
                self.clip_error=None
                return result
            except Exception as exc:
                self.clip_error=f'{type(exc).__name__}: {exc}'
                raise

    def index(self, store, frame):
        import numpy as np
        existing=store.rows('SELECT body FROM embeddings WHERE evidence=? AND version=?',(frame['id'],EMBED_VERSION))
        if existing: return
        vector=self.embedding(image=store.asset(frame['asset']))
        folder=store.root/'embeddings';folder.mkdir(exist_ok=True)
        filename=hashlib.sha256((EMBED_VERSION+frame['id']).encode()).hexdigest()+'.npy'
        np.save(folder/filename,vector,allow_pickle=False)
        body={'asset':f'embeddings/{filename}','version':EMBED_VERSION,'source_sha256':frame['sha256']}
        store.execute('INSERT OR IGNORE INTO embeddings VALUES(?,?,?)',(frame['id'],EMBED_VERSION,json.dumps(body)))

    def search(self, store, query, session, start=0, end=None, limit=6):
        import numpy as np
        if not query.strip() or len(query)>1000 or not 1<=limit<=8: raise ValueError('Query required; limit 1..8')
        q=self.embedding(text=query)
        heap=[]; candidate_count=0
        rows=store.iter_rows('''SELECT e.body,v.body FROM evidence e JOIN embeddings v ON e.id=v.evidence
            WHERE e.session=? AND e.elapsed>=? AND e.elapsed<=? AND v.version=?''',
            (session,start,end if end is not None else 2**62,EMBED_VERSION))
        for raw,emb in rows:
            frame=json.loads(raw);meta=json.loads(emb)
            if meta['source_sha256'] != frame['sha256']: continue
            vector=np.load(store.asset(meta['asset']),allow_pickle=False)
            candidate_count += 1
            heapq.heappush(heap, (float(vector@q), frame['id'], frame))
            if len(heap)>limit: heapq.heappop(heap)
        results=[{'similarity':score,'evidence':frame} for score,_,frame in sorted(heap,reverse=True)]
        return {'results':results,'indexed_candidates':candidate_count,'embedding_version':EMBED_VERSION,
                'notice':'Similarity ranks candidates, not probability or proof.',
                'text_matches':store.text_search(query,session,limit)}

    def load_edge(self, device_override=None):
        if self.predictor is not None: return
        source=self.root/'EdgeTAM'
        if not (source/'sam2').exists() or not (self.root/'edgetam.pt').is_file():
            raise ValueError('EdgeTAM source/weights missing. Run fieldnotes setup-memory-models.')
        revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
        if revision != EDGE_CODE_REVISION: raise ValueError('EdgeTAM source revision does not match pinned revision')
        if str(source) not in sys.path: sys.path.insert(0,str(source))
        from sam2.build_sam import build_sam2_video_predictor
        import torch
        torch.set_num_threads(2)
        # EdgeTAM has produced non-finite MPS outputs on this setup. Keep its
        # policy separate from MobileCLIP, which can still use MPS.
        requested=device_override or os.getenv('FIELDNOTES_TRACK_DEVICE','cpu')
        if requested not in ('auto','mps','cpu'):
            raise ValueError('Tracking device must be auto, mps or cpu')
        device='mps' if requested=='mps' and torch.backends.mps.is_available() else 'cpu'
        # Disable optional CUDA connected-components postprocessing; temporal predictor is unchanged.
        self.predictor=build_sam2_video_predictor('configs/edgetam.yaml',str(self.root/'edgetam.pt'),
            device=device,apply_postprocessing=False)
        self.edge_device=device

    def track_chunk(self, frames, store, points, labels, previous_mask=None):
        """Run temporal inference on at most 8 frames. Caller preserves timestamps."""
        with self.lock:
            try:
                import numpy as np
                import torch
                self.load_edge()
                with tempfile.TemporaryDirectory(prefix='fieldnotes-track-') as temp:
                    for i,frame in enumerate(frames):
                        shutil.copyfile(store.asset(frame['asset']),Path(temp)/f'{i:06d}.jpg')
                    def infer():
                        with torch.inference_mode():
                            # Keep predictor state on the model device, including any
                            # opt-in MPS run; CPU video decoding is transferred upstream.
                            state=self.predictor.init_state(temp,offload_video_to_cpu=True,offload_state_to_cpu=False)
                            if previous_mask is not None:
                                self.predictor.add_new_mask(state,frame_idx=0,obj_id=1,mask=previous_mask)
                            else:
                                self.predictor.add_new_points_or_box(state,frame_idx=0,obj_id=1,
                                    points=np.asarray(points,dtype=np.float32),labels=np.asarray(labels,dtype=np.int32))
                            output=[]
                            for idx,ids,logits in self.predictor.propagate_in_video(state):
                                scores=logits[0,0].float().cpu().numpy()
                                if not np.isfinite(scores).all():
                                    raise RuntimeError('Non-finite EdgeTAM logits; invalid mask rejected')
                                output.append((idx,scores>0,float(np.mean(np.abs(scores),dtype=np.float64))))
                            del state
                            return output
                    try: result=infer()
                    except (RuntimeError,NotImplementedError) as exc:
                        if self.edge_device!='mps': raise
                        self.fallbacks.append('EdgeTAM CPU fallback: '+str(exc)[:200])
                        self.predictor=None
                        self.load_edge('cpu')
                        result=infer()
                self.edge_error=None
                return result
            except Exception as exc:
                self.edge_error=f'{type(exc).__name__}: {exc}'
                raise
