"""Source metadata filters cached rows without touching network or storage."""
MODES = ('MIXED', 'DE', 'EN')

def filter_rows(rows, feeds, mode='MIXED', category='ALL'):
    languages = {f['url']: f.get('language', 'unknown') for f in feeds}
    return [r for r in rows if (category == 'ALL' or r['category'] == category)
            and (mode == 'MIXED' or languages.get(r['feed'], 'unknown') == mode.lower())]
