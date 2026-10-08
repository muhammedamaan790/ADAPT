"""Render documentation art only. Requires Pillow; imageio-ffmpeg is optional for MP4.

Usage: python render_assets.py --font-dir /path/to/fonts
Use --font-dir to select a directory containing segoeui.ttf and segoeuib.ttf.
Source values: adapt/web/src/api/{fixtures,fixture-service}.ts, DEMO_01.
No application or advertising APIs are called. Frames live in a temporary directory.
"""
from pathlib import Path
import argparse
import json
import math
import tempfile
import subprocess
from PIL import Image, ImageDraw, ImageFont

P = Path(__file__).parent
W, H, FPS, COUNT = 1200, 660, 24, 174
BG, INK, MUTED, CYAN, BLUE, LINE = '#14181d', '#f3f5f6', '#a8b4c1', '#63dcf2', '#5195ff', '#303a46'

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--fixture', type=Path, default=P/'story-data.json')
ap.add_argument('--font-dir', type=Path, default=Path('C:/Windows/Fonts'))
args = ap.parse_args()
data = json.loads(args.fixture.read_text(encoding='utf-8-sig'))
decision, evidence = data['decision'], data['evidence']
outcome=data['outcome']
assert [x['after'] for x in decision['legs']] == [32000, 28800, 22400, 12800]
assert decision['expected']['calibrated_pred'] == 7200
assert evidence['chart'][-1]['actual'] == 2.3
assert outcome['measured'] == 5600 and round(outcome['factor_after'],2) == 0.86

def font(size, bold=False):
    name = 'segoeuib.ttf' if bold else 'segoeui.ttf'
    return ImageFont.truetype(str(args.font_dir / name), size)

def text(d, xy, s, size=24, color=INK, bold=False):
    d.text(xy, s, font=font(size, bold), fill=color)

def mark(d, x, y, size, color):
    for pts in [[(32,80),(80,80),(80,178),(176,178),(176,224),(80,224),(32,176)],
                [(112,32),(176,32),(224,80),(224,144),(176,144),(176,78),(112,78)]]:
        d.polygon([(x+a*size/256,y+b*size/256) for a,b in pts], fill=color)

def ease(x):
    x=max(0,min(1,x)); return x*x*(3-2*x)

def rupees(n): return '₹'+f'{round(n):,}'

def base(stage, progress):
    im=Image.new('RGB',(W,H),BG); d=ImageDraw.Draw(im)
    mark(d,38,27,52,INK); text(d,(103,27),'ADAPT',34,bold=True)
    text(d,(750,36),'THE DECISION LOOP / DEMO_01',21,MUTED)
    steps=['OBSERVE','DIAGNOSE','DECIDE','ACT','LEARN']
    for i,s in enumerate(steps):
        x=48+i*230
        text(d,(x,111),s,22,CYAN if i==stage else MUTED,bold=i==stage)
        d.line((x,154,x+190,154),fill=LINE,width=2)
        if i==stage: d.line((x,154,x+max(2,int(190*progress)),154),fill=CYAN,width=3)
    d.line((48,600,1152,600),fill=LINE,width=1)
    text(d,(48,618),'ILLUSTRATIVE FRONTEND FIXTURES · NO LIVE AD ACCOUNT',19,MUTED)
    text(d,(940,618),'DataQuest 3.0',19,MUTED)
    return im,d

def scene(stage, p):
    im,d=base(stage,p)
    if stage==0:
        text(d,(48,185),'An efficiency shift.',43,bold=True)
        text(d,(48,246),'Hero · Prospecting / reconciled ROAS',24,MUTED)
        text(d,(48,327),'2.30×',86,bold=True)
        text(d,(52,439),'3.80× baseline forecast',25,MUTED)
        text(d,(52,497),'Performance change → investigate',25,CYAN)
        pts=evidence['chart']; x0,x1,y0,y1=520,1132,304,503
        for val in [2.3,3.0,3.8]:
            y=y1-(val-2.0)/2.0*(y1-y0)
            d.line((x0,y,x1,y),fill=LINE,width=1)
        def coords(k): return [(x0+i*(x1-x0)/13, y1-(r[k]-2)/2*(y1-y0)) for i,r in enumerate(pts)]
        d.line(coords('baseline'),fill=MUTED,width=2)
        d.line(coords('actual'),fill=CYAN,width=5)
        x,y=coords('actual')[-1];d.ellipse((x-6,y-6,x+6,y+6),fill=CYAN)
        text(d,(520,532),'Actual',23,CYAN);text(d,(885,532),'Baseline',23,MUTED)
    elif stage==1:
        text(d,(48,185),'A likely driver. A hard constraint.',43,bold=True)
        d.line((614,293,614,547),fill=LINE,width=1)
        text(d,(48,296),'CREATIVE FATIGUE',23,CYAN,bold=True)
        text(d,(48,348),'−40%',76,bold=True)
        text(d,(48,444),'Hero creative click-through rate',25,MUTED)
        text(d,(48,491),'Sibling creatives within ±4%',25,MUTED)
        text(d,(663,296),'INVENTORY EXPOSURE',23,CYAN,bold=True)
        text(d,(663,348),'42 units',68,bold=True)
        text(d,(663,444),'Projected shortfall before action',25,MUTED)
        text(d,(663,491),'BLOCK SCALE',25,'#ffcf87',True)
        text(d,(48,555),'Probable driver · not causal proof',20,MUTED)
    elif stage in (2,3):
        act=stage==3; q=ease(p) if act else 0
        text(d,(48,185),'Move the budget. Keep the guardrails.' if act else 'A proposal, ready for review.',40,bold=True)
        text(d,(48,249),'₹11,200/day released → ₹7,200 redistributed + ₹4,000 held back',24,CYAN)
        names=['Meta / Hero','Google / Bundle','Meta / Refill','Google / Brand']
        colors=[BLUE,CYAN,'#b8c6d8','#668ab2']
        for i,(leg,name,col) in enumerate(zip(decision['legs'],names,colors)):
            y=307+57*i; value=leg['before']+(leg['after']-leg['before'])*q
            text(d,(48,y),name,24,INK,True)
            d.rounded_rectangle((315,y+5,715,y+25),10,fill='#232c37')
            d.rounded_rectangle((315,y+5,315+400*value/40000,y+25),10,fill=col)
            text(d,(753,y),rupees(value),25,INK,True)
            text(d,(952,y),f"→ {rupees(leg['after'])}" if not act else ('−20%' if i in (0,3) else '+20%' if i==1 else '+12%'),23,MUTED)
        if act:
            # Directed transfers from two donors into two receivers and the cash reserve.
            for source,target,delay in [(0,1,0),(0,2,.18)]:
                t=(p*1.5-delay)%1; x=315+400*t
                y=322+57*source+57*(target-source)*ease(t)
                d.ellipse((x-6,y-6,x+6,y+6),fill=INK)
            # One leg's transfer is carried visibly, not represented by decorative particles.
            t=ease(p); px=340+305*t; py=310+57*t
            d.rounded_rectangle((px-5,py-15,px+114,py+26),8,fill=INK)
            text(d,(px+6,py-14),'₹4,800 →',22,BG,True)
        text(d,(48,554),'Human approval → mock execution → read-back',22,MUTED)
        text(d,(759,554),'+₹7,200 / 3 days',24,CYAN,True)
    else:
        text(d,(48,185),'The outcome changes the next estimate.',40,bold=True)
        text(d,(48,291),'FORECAST / 3 DAYS',23,MUTED)
        text(d,(48,338),'+₹7,200',76,bold=True)
        text(d,(669,291),'ILLUSTRATIVE OUTCOME',23,CYAN)
        text(d,(669,338),'+₹5,600',76,CYAN,True)
        d.line((48,455,1152,455),fill=LINE,width=1)
        text(d,(48,489),'Optimism correction',25,MUTED)
        text(d,(760,480),'0.90 → 0.86',43,INK,True)
        text(d,(48,552),'Feedback applied once · fixture comparison, not observed uplift',22,MUTED)
    return im

def hero(dark):
    bg,ink,muted,accent = (BG,INK,MUTED,CYAN) if dark else ('#fafaf8','#191d22','#526170','#067b98')
    im=Image.new('RGB',(1200,470),bg);d=ImageDraw.Draw(im)
    mark(d,39,27,69,ink);text(d,(126,33),'ADAPT',45,ink,True)
    text(d,(50,141),'Every budget move.',61,ink,True)
    text(d,(50,212),'A reason behind it.',61,ink,True)
    text(d,(53,318),'D2C advertising intelligence & decision engine',25,muted)
    # A precise editorial diagram, not a fabricated dashboard screenshot.
    d.line((760,95,760,349),fill='#35414c' if dark else '#cbd5d9',width=1)
    text(d,(804,123),'SIGNAL',21,muted)
    text(d,(804,158),'Creative fatigue',29,ink,True)
    text(d,(804,231),'DECISION',21,muted)
    text(d,(804,266),'Allocate with evidence',27,accent,True)
    d.line((52,396,1148,396),fill='#35414c' if dark else '#cbd5d9',width=1)
    text(d,(53,422),'OBSERVE → DIAGNOSE → DECIDE → ACT → LEARN',23,accent,True)
    return im

hero(True).save(P/'hero-dark.png',optimize=True)
hero(False).save(P/'hero-light.png',optimize=True)
# Static, stacked story for narrow screens. Essential information also exists as README text.
mobile=Image.new('RGB',(720,1080),BG);md=ImageDraw.Draw(mobile)
mark(md,31,24,58,INK);text(md,(108,27),'ADAPT / THE DECISION LOOP',27,INK,True)
rows=[('OBSERVE','2.30× ROAS','Against a 3.80× baseline'),
      ('DIAGNOSE','Creative CTR −40%','42-unit inventory shortfall'),
      ('DECIDE','₹96,000/day allocated','₹4,000/day held back'),
      ('ACT','Approve → execute → verify','Frontend example; no live account'),
      ('LEARN','₹7,200 forecast / ₹5,600 outcome','Illustrative 3-day comparison')]
for i,(label,value,description) in enumerate(rows):
    y=130+i*171
    text(md,(40,y),label,24,CYAN,True)
    text(md,(40,y+37),value,34,INK,True)
    text(md,(40,y+86),description,27,MUTED)
    md.line((40,y+140,680,y+140),fill=LINE,width=1)
text(md,(40,1016),'ILLUSTRATIVE FRONTEND FIXTURES',25,MUTED)
mobile.save(P/'decision-loop-mobile.png',optimize=True)
frames=[]
for i in range(COUNT):
    t=i/FPS
    if t<1.35: stage,p=0,t/1.35
    elif t<2.7: stage,p=1,(t-1.35)/1.35
    elif t<3.8: stage,p=2,(t-2.7)/1.1
    elif t<5.6: stage,p=3,(t-3.8)/1.8
    else: stage,p=4,(t-5.6)/1.2
    im=scene(stage,p)
    # Short eased crossfades carry the viewer through stages without hard scene cuts.
    if stage and p < 0.14:
        im=Image.blend(scene(stage-1,1),im,ease(p/0.14))
    if t>6.7: im=Image.blend(im,scene(0,0),ease((t-6.7)/(173/24-6.7)))
    frames.append(im)
frames[-1]=frames[0].copy()
frames[0].save(P/'decision-loop-poster.png',optimize=True)
# One shared palette prevents frame-to-frame color flicker. GIF timing is 10 ms quantized.
samples=Image.new('RGB',(600,330*6))
for j,i in enumerate([0,40,70,105,128,150]):samples.paste(frames[i].resize((600,330)),(0,j*330))
palette=samples.quantize(colors=128,method=Image.Quantize.MEDIANCUT)
gif=[im.quantize(palette=palette,dither=Image.Dither.NONE) for im in frames]
durations=[(round((i+1)*100/FPS)-round(i*100/FPS))*10 for i in range(COUNT)]
gif[0].save(P/'adapt-decision-loop.gif',save_all=True,append_images=gif[1:],loop=0,
            duration=durations,optimize=True,disposal=1)
# A compact higher quality H.264 version is convenient outside GitHub's image renderer.
try:
    import imageio_ffmpeg
    with tempfile.TemporaryDirectory(prefix='adapt-readme-',dir=P) as tmp:
        for i,im in enumerate(frames):im.save(Path(tmp)/f'{i:04}.png')
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-framerate',str(FPS),
                        '-i',str(Path(tmp)/'%04d.png'),'-c:v','libx264','-crf','20','-pix_fmt','yuv420p',
                        '-movflags','+faststart',str(P/'adapt-decision-loop.mp4')],check=True)
except ImportError:
    print('MP4 omitted: optional imageio-ffmpeg unavailable.')
print(json.dumps({'duration_seconds':sum(durations)/1000,'frames':COUNT,
                  'gif_bytes':(P/'adapt-decision-loop.gif').stat().st_size,'seamless_endpoints':frames[0].tobytes()==frames[-1].tobytes()}))
