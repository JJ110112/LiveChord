import os
from pathlib import Path
import re

frontend_dir = Path("c:/Users/hitea/Claude/LiveChord/frontend")

html_files = list(frontend_dir.glob("*.html"))

old_script = """  <script>
    if (!localStorage.getItem("livechord_token")) {
      window.location.href = "/login";
    }
  </script>"""

new_script = """  <script>
    if (!localStorage.getItem("livechord_token")) {
      fetch('/api/auth/is_admin').then(r => {
        if (!r.ok) window.location.href = "/login";
      }).catch(() => {
        window.location.href = "/login";
      });
    }
  </script>"""

for file in html_files:
    if file.name in ["login.html", "tos.html"]:
        continue
        
    content = file.read_text(encoding="utf-8")
    
    # Try literal replace first
    if old_script in content:
        content = content.replace(old_script, new_script)
        file.write_text(content, encoding="utf-8")
        print(f"Patched {file.name}")
    else:
        # Fallback regex search for similar pattern
        pattern = re.compile(r"<script>[\s\n]*if \(!localStorage\.getItem\([\"']livechord_token[\"']\)\) \{[\s\n]*window\.location\.href = [\"']/login[\"'];[\s\n]*\}[\s\n]*</script>")
        if pattern.search(content):
            content = pattern.sub(new_script, content)
            file.write_text(content, encoding="utf-8")
            print(f"Patched {file.name} (Regex)")
        else:
            print(f"Pattern not found in {file.name}")
