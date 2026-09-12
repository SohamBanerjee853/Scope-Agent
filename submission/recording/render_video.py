"""Render recorded terminal cells into a 1080p screencast and speaking script."""
from pathlib import Path
import argparse
import bisect
import gzip
import hashlib
import json
import re
import subprocess
import textwrap

import imageio_ffmpeg
import pyte
from PIL import Image, ImageDraw, ImageFont

BASE=Path(__file__).resolve().parent
OUT=BASE/'deliverables'
OUT.mkdir(exist_ok=True)
FONT='/System/Library/Fonts/Menlo.ttc'
SANS='/System/Library/Fonts/Supplemental/Arial.ttf'
BOLD='/System/Library/Fonts/Supplemental/Arial Bold.ttf'
MONO=ImageFont.truetype(FONT,20)
MONOBOLD=ImageFont.truetype(FONT,20,index=1)
TITLE=ImageFont.truetype(BOLD,30)
LABEL=ImageFont.truetype(BOLD,18)
SUBTITLE=ImageFont.truetype(SANS,24)
META=ImageFont.truetype(SANS,18)
NAMED={'black':'000000','red':'cd0000','green':'00cd00','brown':'cdcd00','blue':'0000ee',
       'magenta':'cd00cd','cyan':'00cdcd','white':'e5e5e5','brightblack':'7f7f7f',
       'brightred':'ff0000','brightgreen':'00ff00','brightbrown':'ffff00','brightblue':'5c5cff',
       'brightmagenta':'ff00ff','brightcyan':'00ffff','brightwhite':'ffffff'}
NARRATION=json.loads((BASE/'narration.json').read_text())

def stamp(seconds, srt=False):
    if srt:
        ms=round(seconds*1000)
        return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    return f'{int(seconds)//60}:{int(seconds)%60:02}'

def color(value, default):
    if value=='default': return default
    return '#'+NAMED.get(value,value)

def pane(ansi, cols):
    screen=pyte.Screen(cols,34)
    stream=pyte.Stream(screen)
    stream.feed(ansi.removesuffix('\n').replace('\n','\r\n'))
    im=Image.new('RGB',(cols*12,34*24),'#101820')
    d=ImageDraw.Draw(im)
    for row in range(34):
        for col in range(cols):
            ch=screen.buffer[row][col]
            fg=color(ch.fg,'#edf2f7'); bg=color(ch.bg,'#101820')
            if ch.reverse: fg,bg=bg,fg
            x,y=col*12,row*24
            d.rectangle((x,y,x+11,y+23),fill=bg)
            if ch.data.strip():
                d.text((x,y),ch.data,font=MONOBOLD if ch.bold else MONO,fill=fg)
            if ch.underscore: d.line((x,y+22,x+11,y+22),fill=fg)
    return im

def cues():
    result=[]
    for section in NARRATION:
        sentences=re.split(r'(?<=[.!?])\s+',section['text'])
        total=sum(len(x.split()) for x in sentences)
        start=section['start']
        for i,sentence in enumerate(sentences):
            end=section['end'] if i==len(sentences)-1 else start+(section['end']-section['start'])*len(sentence.split())/total
            result.append({'start':start,'end':end,'speaker':section['speaker'],'text':sentence})
            start=end
    return result

CUES=cues()

def script():
    shots=[
        'Buggy retry key on the left; actual Scope review inbox on the right.',
        'Open the permission card, review its exact command and budget, select Approve scope.',
        'Show the actual T3 classification and empty hook decision for an inert git push request.',
        'Enter prediction 1 and its reason; save it; open the separate consent form and choose Run once.',
        'Show observed charge count 2, mismatched prediction, and the original failing regression.',
        'Choose the smaller next step, then show the actual stable-order-key code change.',
        'Save a fresh prediction, separately consent again, then show one charge and three passing tests.',
        'Press Ctrl-r, open a new matching request, pass it to the host, and show the receipt.',
        'Hold the receipt and the closing line.']
    words=sum(len(x['text'].split()) for x in NARRATION)
    lines=['# Scope — two-minute submission script','',
        f'Runtime: **2:00**. Spoken script: **{words} words**. Soham: 126 words / 58 seconds. Arjun: 122 words / 62 seconds.','',
        'The video is silent screen footage for your own voices. Read naturally at roughly 124 words per minute; the time ranges include breathing room.','',
        '| Time | Speaker | Say | On screen |','| --- | --- | --- | --- |']
    for section,shot in zip(NARRATION,shots):
        lines.append(f"| {stamp(section['start'])}–{stamp(section['end'])} | **{section['speaker']}** | {section['text']} | {shot} |")
    lines += ['', '## Recording notes','',
        '- Record your own two voices over the silent MP4. Keep the handoffs at 0:40 and 1:42.',
        '- Use the clean MP4 if your editor will add subtitles or presenter camera footage; use the captioned version for a readable standalone walkthrough.',
        '- Keep faces outside the command cards and form fields. The footage is 1920×1080, H.264, 10 fps and exactly 120 seconds.',
        '- The left pane is a scripted demo driver; the right pane is the actual Scope interface. The local payment code, permission hooks, probes, tests, next-step fixture handoff and receipt all ran.',
        '- Do not call this a live Codex/Claude session, a real payment integration, measured human learning, or a benchmark of time saved. No live model was launched; reviewer inputs and the repair were scripted.',
        '- Sponsor integrations that are only proposed are deliberately absent from the pitch.','',
        '## Evidence shown','',
        '- Before repair: two local charges; one failed and two passed regression tests.',
        '- After repair: one local charge; three passed regression tests.',
        '- One bounded scope, two scoped allowances, one T3 hard ask, and successful revocation.',
        '- Two predictions, two separately consented probe executions, and two recorded observations.',
        '- The synthetic push was an input to the permission hook only; it never executed.','']
    (OUT/'soham-arjun-script.md').write_text('\n'.join(lines))
    (OUT/'soham-arjun-script.txt').write_text('\n\n'.join(
        f"{stamp(x['start'])}–{stamp(x['end'])}  {x['speaker'].upper()}\n{x['text']}" for x in NARRATION)+'\n')
    (OUT/'scope-submission.srt').write_text('\n\n'.join(
        f"{i+1}\n{stamp(x['start'],True)} --> {stamp(x['end'],True)}\n{x['speaker']}: {x['text']}" for i,x in enumerate(CUES))+'\n')

def render(take, *, preview_only=False):
    with gzip.open(take/'terminal-frames.jsonl.gz','rt') as stream:
        frames=[json.loads(line) for line in stream]
    times=[x['t'] for x in frames]
    memo={}
    def base_frame(t):
        frame=frames[max(0,bisect.bisect_right(times,t)-1)]
        section=next(x for x in NARRATION if x['start']<=t<x['end'])
        im=Image.new('RGB',(1920,1080),'#0b1017')
        d=ImageDraw.Draw(im)
        d.text((48,22),'SCOPE  /  Checkout retry investigation',font=TITLE,fill='#eef3f8')
        chapter=section['chapter']
        d.text((1872-d.textlength(chapter,font=LABEL),30),chapter,font=LABEL,fill='#9ee8dd')
        d.text((48,64),'LOCAL PAYMENT FIXTURE  ·  SCRIPTED DRIVER & REVIEW INPUTS  ·  REAL LOCAL EXECUTION',font=META,fill='#a4b5c5')
        d.text((72,99),'DEBUGGING WORKFLOW  /  DEMO DRIVER',font=LABEL,fill='#a4b5c5')
        d.text((888,99),'SCOPE  /  ACTUAL REVIEW PANE',font=LABEL,fill='#9ee8dd')
        for name,cols,x in [('left',66,72),('right',81,888)]:
            key=(name,frame[name])
            if key not in memo: memo[key]=pane(frame[name],cols)
            im.paste(memo[key],(x,124))
        d.line((864,124,864,940),fill='#31404f',width=2)
        d.rectangle((48,1062,1872,1065),fill='#233242')
        d.rectangle((48,1062,48+int(1824*t/120),1065),fill='#9ee8dd')
        d.text((1790,982),stamp(t)+' / 2:00',font=META,fill='#a4b5c5')
        return im
    def captioned(im,t):
        im=im.copy(); d=ImageDraw.Draw(im)
        cue=next(x for x in CUES if x['start']<=t<x['end'])
        d.text((72,970),cue['speaker'].upper(),font=LABEL,fill='#9ee8dd')
        words=cue['text'].split(); lines=[]; current=''
        for word in words:
            candidate=(current+' '+word).strip()
            if d.textlength(candidate,font=SUBTITLE)>1430:
                lines.append(current); current=word
            else: current=candidate
        if current: lines.append(current)
        assert len(lines)<=2, lines
        for i,line in enumerate(lines): d.text((236,968+30*i),line,font=SUBTITLE,fill='#eef3f8')
        return im
    script()
    for t in (18,50,59,69,81,98,116):
        captioned(base_frame(t),t).save(OUT/f'frame-{t:03}.png')
    if preview_only: return
    executable=imageio_ffmpeg.get_ffmpeg_exe()
    processes=[]; logs=[]
    for name in ('scope-submission-clean.mp4','scope-submission-captioned.mp4'):
        log=(OUT/(name+'.encode.log')).open('wb'); logs.append(log)
        processes.append(subprocess.Popen([executable,'-y','-f','rawvideo','-pixel_format','rgb24',
            '-video_size','1920x1080','-framerate','10','-i','pipe:0','-an',
            '-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',
            str(OUT/name)],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log))
    try:
        for i in range(1200):
            t=i/10
            im=base_frame(t)
            processes[0].stdin.write(im.tobytes())
            processes[1].stdin.write(captioned(im,t).tobytes())
            if i%200==0: print(f'Encoded {t:.0f}/120 seconds',flush=True)
        for process in processes:
            process.stdin.close()
            assert process.wait()==0
    finally:
        for process in processes:
            if process.poll() is None: process.terminate()
        for log in logs: log.close()
    (OUT/'receipt.json').write_bytes((take/'receipt.json').read_bytes())
    report=json.loads((take/'evidence.json').read_text())
    manifest={'source_commit':'cd8f4209eaf6a4b7ea4991b0aa4dd045ccbb51e8','source_take':str(take),
       'video_seconds':120,'width':1920,'height':1080,'fps':10,'frames':1200,'audio':'none; record the supplied script',
       'capture':'Continuous actual tmux pane ANSI viewports, rendered to pixels; not a desktop camera capture or invented UI.',
       'recording':json.loads((take/'recording.json').read_text()),'evidence_verified':report['verified'],
       'limits':report['limits'],'sha256':{name:hashlib.sha256((OUT/name).read_bytes()).hexdigest()
                  for name in ('scope-submission-clean.mp4','scope-submission-captioned.mp4')}}
    (OUT/'recording-manifest.json').write_text(json.dumps(manifest,indent=2))
    print(OUT,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('take',type=Path); parser.add_argument('--preview-only',action='store_true')
    args=parser.parse_args(); render(args.take,preview_only=args.preview_only)
