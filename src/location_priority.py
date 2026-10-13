"""Location priority uses explicit vacancy location text, never company HQ."""
import re,unicodedata

def normalize(value):
    text=str(value or '').lower().replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    return ''.join(c for c in unicodedata.normalize('NFKD',text) if not unicodedata.combining(c))

def contains(text,word):
    return re.search(r'(?<!\w)'+re.escape(normalize(word))+r'(?!\w)',normalize(text)) is not None

PREFERRED={
 'Taufkirchen':['taufkirchen'],
 'Unterhaching':['unterhaching'],
 'Ottobrunn':['ottobrunn'],
 'Unterfoehring':['unterfoehring','unterföhring'],
 'Bogenhausen':['bogenhausen'],
 'Werksviertel':['werksviertel'],
 'Lehel':['lehel'],
 'Downtown':['altstadt','innenstadt','city centre','city center','downtown','marienplatz','odeonsplatz','karlsplatz','sendlinger tor'],
}
MUNICH_LABELS=['munich','münchen','muenchen','munich metropolitan region','metropolregion münchen']
REGIONAL_LABELS=['augsburg','ingolstadt','kaufbeuren','landshut','altötting','bad tölz','wolfratshausen','dachau','dillingen an der donau','dingolfing','landau an der isar','donau-ries','ebersberg','freising','fürstenfeldbruck','garmisch-partenkirchen','kelheim','landsberg am lech','miesbach','mühldorf am inn','ostallgäu','pfaffenhofen an der ilm','starnberg']

def area_priority(location):
    for label,terms in PREFERRED.items():
        if any(contains(location,t) for t in terms):
            # Other towns share the name Taufkirchen: do not prioritise
            # an explicit contradictory district as the Hachinger Tal site.
            if label=='Taufkirchen' and any(contains(location,t) for t in ['vils','erding','mühldorf']):continue
            return 0,label
    if any(contains(location,t) for t in ['munich','münchen','muenchen']):return 1,'Munich (area unspecified)'
    return 2,'Wider region / area unspecified'

def sort_key(location,score):
    rank,_=area_priority(location)
    return rank,-(score if isinstance(score,(int,float)) else 0)
