"""Render compact SVG side-view and trajectory overlays from audit JSON."""
import json,sys
from pathlib import Path

def main():
    src=Path(sys.argv[1]);out=Path(sys.argv[2]);d=json.loads(src.read_text(encoding='utf-8'))
    v5=json.loads(Path(sys.argv[3]).read_text(encoding='utf-8')) if len(sys.argv)>3 else None
    W,H=1200,760;parts=[]
    def line(x1,y1,x2,y2,color,width=2,dash=''):
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{width}" {f"stroke-dasharray={chr(34)}{dash}{chr(34)}" if dash else ""}/>')
    def txt(x,y,s,size=14,color='#222'):
        parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="Segoe UI,Arial" font-size="{size}" fill="{color}">{s}</text>')
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><rect width="100%" height="100%" fill="white"/>')
    txt(30,34,'Luigi walk vs V4 Waluigi leg trajectories (rest-calibrated anatomical coordinates)',22)
    txt(30,58,'Side view: forward vs up, each rig normalized by its own upper+lower leg length. Dots mark 0, 15, 30, 45, 60.',13,'#555')
    colors={'source':'#087e8b','v4':'#cf3f2e','approved_baseline':'#888888','v5':'#5b8c27'}
    names=['source','v4','approved_baseline']+(['v5'] if v5 else [])
    v5rows={r['frame']:r for r in v5['final_leg_trajectories']} if v5 else {}
    def ankle(frame,side,name):
        row=d['frames'][str(float(frame))][side]
        if name=='source':return row['source']['ankle']
        if name=='v5':return v5rows[float(frame)]['sides'][side]['target_ankle']
        p=row[name];return [p['ankle'][i]-p['hip'][i] for i in range(3)]
    rows=[d['frames'][k] for k in sorted(d['frames'],key=float)]
    for si,side in enumerate(('left','right')):
        x0=55+si*590;y0=110;pw=500;ph=260
        txt(x0,y0-18,side.title()+' leg',18)
        vals=[ankle(float(row['frame']),side,name) for row in rows for name in names]
        xs=[p[2] for p in vals];ys=[p[1] for p in vals]
        xmin,xmax=min(xs)-.08,max(xs)+.08;ymin,ymax=min(ys)-.08,max(ys)+.08
        def xy(p):return x0+(p[2]-xmin)/(xmax-xmin)*pw,y0+ph-(p[1]-ymin)/(ymax-ymin)*ph
        line(x0,y0+ph,x0+pw,y0+ph,'#bbb');line(x0,y0,x0,y0+ph,'#bbb')
        for i in range(5):
            xx=x0+i*pw/4;yy=y0+ph-i*ph/4
            line(xx,y0,xx,y0+ph,'#eee',1);line(x0,yy,x0+pw,yy,'#eee',1)
            txt(xx-15,y0+ph+19,f'{xmin+(xmax-xmin)*i/4:.2f}',10,'#777')
            txt(x0-37,yy+4,f'{ymin+(ymax-ymin)*i/4:.2f}',10,'#777')
        txt(x0+pw/2-45,y0+ph+38,'forward (leg lengths)',12)
        for name in names:
            pts=[xy(ankle(float(row['frame']),side,name)) for row in rows]
            parts.append('<polyline fill="none" stroke="%s" stroke-width="2.5" points="%s"/>'%(colors[name],' '.join(f'{x:.2f},{y:.2f}' for x,y in pts)))
            for idx in [0,60,120,180,240]:
                x,y=pts[idx];parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colors[name]}"/>')
        txt(x0+pw-225,y0+16,'Luigi / V4 / baseline'+(' / V5' if v5 else ''),12)
    txt(30,450,'Motion traces over frames 0–60',18)
    for si,side in enumerate(('left','right')):
        x0=60+si*590;y0=480;pw=500;ph=190
        txt(x0,y0-10,side.title(),15)
        allv=[ankle(float(row['frame']),side,name) for row in rows for name in names]
        for axis,label in ((2,'forward'),(1,'up')):
            idx=2 if axis==2 else 1;vals=[p[idx] for p in allv];lo=min(vals)-.05;hi=max(vals)+.05
            top=y0+(0 if axis==2 else 94);hh=88
            line(x0,top+hh,x0+pw,top+hh,'#ddd',1)
            for name in names:
                pts=[]
                for n,row in enumerate(rows):
                    p=ankle(float(row['frame']),side,name);pts.append((x0+n*pw/(len(rows)-1),top+hh-(p[idx]-lo)/(hi-lo)*hh))
                parts.append('<polyline fill="none" stroke="%s" stroke-width="1.8" points="%s"/>'%(colors[name],' '.join(f'{x:.2f},{y:.2f}' for x,y in pts)))
            txt(x0+4,top+13,label,11,'#555')
        for f in (0,15,30,45,60):
            xx=x0+f/60*pw;line(xx,y0,xx,y0+ph,'#eee',1);txt(xx-5,y0+ph+17,str(f),10,'#666')
    parts.append('</svg>');out.parent.mkdir(parents=True,exist_ok=True);out.write_text('\n'.join(parts),encoding='utf-8')
if __name__=='__main__':main()
