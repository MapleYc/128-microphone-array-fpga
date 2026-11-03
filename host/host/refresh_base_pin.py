from pathlib import Path
import hashlib,re
p=Path(__file__).parent/'legacy_adapter.py'
s=p.read_text(encoding='utf8')
h=hashlib.sha256((p.parent/'base/preview.py').read_bytes()).hexdigest()
s=re.sub("('preview.py':')[0-9a-f]+(')",lambda m:m[1]+h+m[2],s)
p.write_text(s,encoding='utf8')
print(h)
