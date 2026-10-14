from pathlib import Path

p = Path('smart-optimizer-ui.py')
s = p.read_text(encoding='utf-8')

def rep(old, new, count=1):
    global s
    n = s.count(old)
    if n < count:
        raise SystemExit(f'missing replacement target ({n} < {count}): {old[:120]!r}')
    s = s.replace(old, new, count)

rep(
'''            "movie_label": str(rqbit.get("movie_label") or "torrentleech-movies").strip(),
            "tv_label": str(rqbit.get("tv_label") or "torrentleech-tv").strip(),
            "other_movie_label": str(rqbit.get("other_movie_label") or "other-indexer-movies").strip(),
            "other_tv_label": str(rqbit.get("other_tv_label") or "other-indexer-tv").strip(),''',
'''            "torrentleech_label": str(rqbit.get("torrentleech_label") or "torrentleech").strip(),
            "radarr_label": str(rqbit.get("radarr_label") or "radarr").strip(),
            "sonarr_label": str(rqbit.get("sonarr_label") or "sonarr").strip(),''',
2,
)

rep(
'''    rqbit_movie_label=None,
    rqbit_tv_label=None,
    rqbit_other_movie_label=None,
    rqbit_other_tv_label=None,''',
'''    rqbit_torrentleech_label=None,
    rqbit_radarr_label=None,
    rqbit_sonarr_label=None,'''
)

rep(
'''    movie_label = str(rqbit_movie_label or current["rqbit"]["movie_label"]).strip()
    tv_label = str(rqbit_tv_label or current["rqbit"]["tv_label"]).strip()
    other_movie_label = str(
        rqbit_other_movie_label or current["rqbit"]["other_movie_label"]
    ).strip()
    other_tv_label = str(
        rqbit_other_tv_label or current["rqbit"]["other_tv_label"]
    ).strip()

    for label in (movie_label, tv_label, other_movie_label, other_tv_label):''',
'''    torrentleech_label = str(
        rqbit_torrentleech_label or current["rqbit"]["torrentleech_label"]
    ).strip()
    radarr_label = str(
        rqbit_radarr_label or current["rqbit"]["radarr_label"]
    ).strip()
    sonarr_label = str(
        rqbit_sonarr_label or current["rqbit"]["sonarr_label"]
    ).strip()

    for label in (torrentleech_label, radarr_label, sonarr_label):'''
)

rep(
'''        "movie_label": movie_label,
        "tv_label": tv_label,
        "other_movie_label": other_movie_label,
        "other_tv_label": other_tv_label,''',
'''        "torrentleech_label": torrentleech_label,
        "radarr_label": radarr_label,
        "sonarr_label": sonarr_label,'''
)

rep(
'''    if policy == "keep_seed":
        label = cfg["movie_label"] if app == "radarr" else cfg["tv_label"]
        return {
            "label": label,
            "seconds": int(cfg["retention_seconds"]),
            "delete_after_verified_import": bool(cfg["retention_enabled"]),
        }

    label = cfg["other_movie_label"] if app == "radarr" else cfg["other_tv_label"]
    return {
        "label": label,
        "seconds": 0,
        "delete_after_verified_import": True,
    }''',
'''    if policy == "keep_seed":
        return {
            "label": cfg["torrentleech_label"],
            "seconds": int(cfg["retention_seconds"]),
            "delete_after_verified_import": bool(cfg["retention_enabled"]),
        }

    label = cfg["radarr_label"] if app == "radarr" else cfg["sonarr_label"]
    return {
        "label": label,
        "seconds": 0,
        "delete_after_verified_import": True,
    }'''
)

rep(
'''  <div class="cfg-row"><label class="cfg-label">rqbit protocol</label><select name="rqbit_scheme"><option value="http"%s>http</option><option value="https"%s>https</option></select></div>''',
'''  <div id="rqbit-settings-block">
  <div class="cfg-row"><label class="cfg-label">rqbit protocol</label><select name="rqbit_scheme"><option value="http"%s>http</option><option value="https"%s>https</option></select></div>'''
)

rep(
'''  <div class="cfg-row"><label class="cfg-label">rqbit movie label</label><input name="rqbit_movie_label" value="%s" required></div>
  <div class="cfg-row"><label class="cfg-label">rqbit TV label</label><input name="rqbit_tv_label" value="%s" required></div>
  <div class="cfg-row"><label class="cfg-label">Other-indexer movie label</label><input name="rqbit_other_movie_label" value="%s" required></div>
  <div class="cfg-row"><label class="cfg-label">Other-indexer TV label</label><input name="rqbit_other_tv_label" value="%s" required></div>''',
'''  <div class="cfg-row"><label class="cfg-label">TorrentLeech label</label><input name="rqbit_torrentleech_label" value="%s" required></div>
  <div class="cfg-help">Any grab from TorrentLeech gets this label, for both movies and TV.</div>
  <div class="cfg-row"><label class="cfg-label">Radarr label</label><input name="rqbit_radarr_label" value="%s" required></div>
  <div class="cfg-row"><label class="cfg-label">Sonarr label</label><input name="rqbit_sonarr_label" value="%s" required></div>'''
)

rep(
'''  <div class="cfg-help">The countdown starts when rqbit marks the torrent complete. Default is 11 days. Smart Optimizer writes the exact label and retention policy into rqbit for each managed torrent.</div>
  <div class="cfg-row"><label class="cfg-label">Deluge protocol</label><select name="deluge_scheme"><option value="http"%s>http</option><option value="https"%s>https</option></select></div>''',
'''  <div class="cfg-help">The countdown starts when rqbit marks a TorrentLeech torrent complete. Non-TorrentLeech grabs use the Radarr/Sonarr label and immediate verified-import cleanup policy.</div>
  </div>
  <div id="deluge-settings-block">
  <div class="cfg-row"><label class="cfg-label">Deluge protocol</label><select name="deluge_scheme"><option value="http"%s>http</option><option value="https"%s>https</option></select></div>'''
)

rep(
'''  <div class="cfg-row"><label class="cfg-label">Deluge password</label><input name="deluge_password" type="password" placeholder="Enter password"></div>''',
'''  <div class="cfg-row"><label class="cfg-label">Deluge password</label><input name="deluge_password" type="password" placeholder="Enter password"></div>
  </div>'''
)

rep(
'''    html.escape(dcfg["rqbit"]["movie_label"], quote=True),
    html.escape(dcfg["rqbit"]["tv_label"], quote=True),
    html.escape(dcfg["rqbit"]["other_movie_label"], quote=True),
    html.escape(dcfg["rqbit"]["other_tv_label"], quote=True),''',
'''    html.escape(dcfg["rqbit"]["torrentleech_label"], quote=True),
    html.escape(dcfg["rqbit"]["radarr_label"], quote=True),
    html.escape(dcfg["rqbit"]["sonarr_label"], quote=True),'''
)

rep(
'''            r_movie_label = (form.get("rqbit_movie_label") or ["torrentleech-movies"])[0]
            r_tv_label = (form.get("rqbit_tv_label") or ["torrentleech-tv"])[0]
            r_other_movie_label = (form.get("rqbit_other_movie_label") or ["other-indexer-movies"])[0]
            r_other_tv_label = (form.get("rqbit_other_tv_label") or ["other-indexer-tv"])[0]''',
'''            r_torrentleech_label = (form.get("rqbit_torrentleech_label") or ["torrentleech"])[0]
            r_radarr_label = (form.get("rqbit_radarr_label") or ["radarr"])[0]
            r_sonarr_label = (form.get("rqbit_sonarr_label") or ["sonarr"])[0]'''
)

rep(
'''                        r_movie_label,
                        r_tv_label,
                        r_other_movie_label,
                        r_other_tv_label,''',
'''                        r_torrentleech_label,
                        r_radarr_label,
                        r_sonarr_label,'''
)

marker = '''</form>\n\n</div>'''
if marker not in s:
    raise SystemExit('settings form close marker not found')
js = '''</form>\n<script>\n(function(){\n  const select = document.querySelector('select[name="selected"]');\n  const rqbit = document.getElementById('rqbit-settings-block');\n  const deluge = document.getElementById('deluge-settings-block');\n  if (!select || !rqbit || !deluge) return;\n  function syncClientFields(){\n    const useRqbit = select.value === 'rqbit';\n    rqbit.style.display = useRqbit ? '' : 'none';\n    deluge.style.display = useRqbit ? 'none' : '';\n  }\n  select.addEventListener('change', syncClientFields);\n  syncClientFields();\n})();\n</script>\n\n</div>'''
s = s.replace(marker, js, 1)

p.write_text(s, encoding='utf-8')
print('patched smart-optimizer-ui.py')
