import base64, json, os, sys, urllib.request

PREFIX = ("Create a 16:9 visual concept for a premium FieldNotes hackathon presentation about physical agency. "
          "Near-black and charcoal background, warm white editorial typography, restrained lime accents, subtle frosted glass, "
          "generous negative space. One clear composition, readable at presentation distance. No invented metrics, capabilities, "
          "logos, or evidence. Distinguish future concepts from implemented features. No text, letters, or numbers in the image. ")

PROMPTS = {
    "cover": PREFIX + ("Cover composition. Large clear empty space on the left for the project name. On the right, a small consumer "
                       "drone overlooking a work area with a workbench, with a restrained visual suggestion of observations across "
                       "time (a faint series of translucent frames). Human-operated prototype, not autonomous manipulation. No people."),
    "problem": PREFIX + ("A workstation viewed through a moving camera, with one work surface partly hidden by a person seen from behind. "
                         "Leave room on the left for a large job question. Convey incomplete visibility without hazard symbols, invented "
                         "missing-object claims, or fake statistics."),
    "roadmap": PREFIX + ("A calm three-horizon roadmap motif: an observation lens motif continuing across time from left to right, "
                         "a faint feedback loop between reasoning and a small bounded observation tool. No delivery-date promises, "
                         "no autonomous manipulation imagery, no completed-feature styling. Abstract, minimal."),
}

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-image-2.5-flare"
out = "C:/Users/Administrator/work/assets"
os.makedirs(out, exist_ok=True)
log = []
for name, prompt in PROMPTS.items():
    body = json.dumps({"model": MODEL, "prompt": prompt, "size": "1536x1024", "quality": "medium", "n": 1}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=body,
                                 headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.load(r)
        img = base64.b64decode(d["data"][0]["b64_json"])
        open(f"{out}/{name}.png", "wb").write(img)
        log.append({"asset": f"{name}.png", "model": MODEL, "prompt": prompt, "status": "ok"})
        print("ok", name, len(img))
    except Exception as e:
        msg = e.read().decode()[:500] if hasattr(e, "read") else str(e)
        log.append({"asset": f"{name}.png", "model": MODEL, "prompt": prompt, "status": "failed", "error": msg})
        print("fail", name, msg)
json.dump(log, open(f"{out}/image_log.json", "w"), indent=2)
