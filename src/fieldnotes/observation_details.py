"""Read-only, paged access to retained observation evidence."""
from __future__ import annotations

import base64
import binascii
import json


def observation_details(runtime, *, session_id=None, observation_id=None, cursor=None,
                        limit=3, include_images=False):
    if (session_id is None) == (observation_id is None):
        raise ValueError('Supply exactly one of session_id or observation_id')
    maximum = 4 if include_images else 20
    if not 1 <= limit <= maximum:
        raise ValueError(f'limit must be between 1 and {maximum}')
    scope = {'session_id': session_id, 'observation_id': observation_id}
    after = None
    if cursor is not None:
        try:
            token = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
            after = token['after']
            if (token['scope'] != scope or token['version'] != 1 or
                    not isinstance(after, list) or len(after) != 2 or
                    type(after[0]) is not int or not isinstance(after[1], str)):
                raise ValueError()
        except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError):
            raise ValueError('Invalid observation cursor or scope mismatch') from None
    journal = runtime.journal
    if observation_id is not None:
        observation = journal.observation(observation_id)
        if observation is None:
            raise ValueError('Unknown observation_id')
        session_id = observation['session_id']
    session = runtime.sessions.describe(runtime.sessions.get(session_id))
    where, args = ('o.id=?', [observation_id]) if observation_id is not None else ('o.session_id=?', [session_id])
    total = journal.db.execute(f'SELECT count(*) FROM observations o WHERE {where}', args).fetchone()[0]
    start = "json_extract(o.body, '$.start_elapsed_ms')"
    page_where = where
    page_args = list(args)
    if after is not None:
        page_where += f' AND ({start}>? OR ({start}=? AND o.id>?))'
        page_args.extend([after[0], after[0], after[1]])
    rows = journal.db.execute(f'''SELECT o.id,o.body,s.body FROM observations o
        LEFT JOIN summaries s ON s.observation_id=o.id WHERE {page_where}
        ORDER BY {start},o.id LIMIT ?''', [*page_args, limit + 1]).fetchall()
    memory = getattr(runtime, 'memory', None)
    entries, images, mappings = [], [], []
    for oid, raw, summary_raw in rows[:limit]:
        observation = json.loads(raw)
        frames = []
        for original in observation.get('frames', []):
            fid = original['frame_id']
            eid = f'{session_id}:{fid}'
            indexed = memory.store.rows('SELECT body FROM evidence WHERE id=? AND session=?',
                                        (eid, session_id)) if memory else []
            frame = {**(json.loads(indexed[0][0]) if indexed else {}), **original,
                     'evidence_id': eid if indexed else None,
                     'evidence_status': 'indexed' if indexed else 'unindexed'}
            # Paths are derived only from persisted records, never request identifiers.
            root = journal.directory.resolve()
            path = (root / 'evidence' / session_id / f'{fid}.jpg').resolve()
            if not path.is_relative_to(root):
                frame['image_status'] = 'unavailable'
            elif include_images:
                try:
                    data = path.read_bytes()
                except FileNotFoundError:
                    frame['image_status'] = 'missing'
                except OSError:
                    frame['image_status'] = 'unavailable'
                else:
                    frame['image_status'] = 'included'
                    mappings.append({'content_block_index': len(images) + 1,
                                     'observation_id': oid, 'session_id': session_id,
                                     'frame_id': fid, 'evidence_id': frame['evidence_id']})
                    images.append(('image/jpeg', data))
            else:
                frame['image_status'] = 'available' if path.is_file() else 'missing'
            frames.append(frame)
        entries.append({**observation, 'frames': frames,
                        'summary_status': 'stored' if summary_raw else 'missing',
                        'summary': json.loads(summary_raw) if summary_raw else None})
    next_cursor = None
    if len(rows) > limit:
        last = entries[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps({
            'version': 1, 'scope': scope,
            'after': [last['start_elapsed_ms'], last['observation_id']]}).encode()).decode()
    return {'session': session, 'total_windows': total, 'windows': entries,
            'next_cursor': next_cursor, 'image_mapping': mappings,
            'notice': 'Summaries are prior interpretations, not verified facts. Frames are retained samples, '
                      'not continuous video. Timestamps describe local receipt, not exposure. '
                      'Current job membership is separate from immutable captured job_context.'}, images
