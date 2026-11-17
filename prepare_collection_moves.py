from pathlib import Path
import json,re,hashlib
base=Path(__file__).resolve().parent
rs=json.loads((base/'tmp/collection_audit/scan.json').read_text(encoding='utf-8'))
root=Path(r'C:/Users/libunak/OneDrive - Reed Elsevier Group ICO Reed Elsevier Inc/Documents/SADM/SMD/Collections/Bulk Collection/COLLECTION_PICKUP_2026-11-06').resolve()
u={'CA5':{'26-20016','26-20413','26-40018','26-50751','26-60811'},'CA6':{'25-1649','26-1900','26-3510','26-5466'}}
plan=[]
for r in rs:
 docket=re.search(r'smd_(\d{2}-\d+)',r['name']).group(1)
 if docket in u.get(r['court'],set()):
  src=Path(r['path']).resolve();dst=root/(r['court']+'U')/r['name']
  assert src.is_relative_to(root) and dst.resolve().is_relative_to(root)
  assert src.is_file() and not dst.exists()
  assert hashlib.sha256(src.read_bytes()).hexdigest()==r['sha256']
  plan.append({'source':str(src),'destination':str(dst),'sha256':r['sha256']})
assert len(plan)==18
(base/'tmp/collection_audit/moves.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
print('Validated 18 moves within collection root; no destination collisions.')
