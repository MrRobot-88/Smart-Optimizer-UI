from pathlib import Path
p=Path('smart-optimizer-ui.py')
s=p.read_text(encoding='utf-8')
script='''<script>\n(function(){\n  const select = document.querySelector('select[name="selected"]');\n  const rqbit = document.getElementById('rqbit-settings-block');\n  const deluge = document.getElementById('deluge-settings-block');\n  if (!select || !rqbit || !deluge) return;\n  function syncClientFields(){\n    const useRqbit = select.value === 'rqbit';\n    rqbit.style.display = useRqbit ? '' : 'none';\n    deluge.style.display = useRqbit ? 'none' : '';\n  }\n  select.addEventListener('change', syncClientFields);\n  syncClientFields();\n})();\n</script>'''
if s.count(script) != 1:
    raise SystemExit(f'expected one misplaced script, found {s.count(script)}')
s=s.replace(script+'\n</section>""" % (', '</section>""" % (', 1)
needle='''  <div class="cfg-help">Saving enables the selected Deluge/rqbit client and disables the other managed client in both Radarr and Sonarr. Other client types are left untouched.</div>\n</form>\n</section>""" % ('''
replacement='''  <div class="cfg-help">Saving enables the selected Deluge/rqbit client and disables the other managed client in both Radarr and Sonarr. Other client types are left untouched.</div>\n</form>\n''' + script + '''\n</section>""" % ('''
if needle not in s:
    raise SystemExit('download client close marker not found')
s=s.replace(needle,replacement,1)
p.write_text(s,encoding='utf-8')
print('moved downloader JS after downloader form')
