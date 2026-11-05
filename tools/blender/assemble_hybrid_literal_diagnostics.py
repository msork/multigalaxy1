"""Assemble reports/contact sheets using only Python's standard library."""
import argparse
import gzip
import json
import math
import os
import struct
import zlib

REGIONS = {
    'left_upper_arm':['bone_22','bone_23','bone_24','bone_25'],
    'left_forearm':['bone_26','bone_27','bone_29'],
    'left_glove':['bone_30']+[f'bone_{x}' for x in range(31,52)],
    'right_upper_arm':['bone_57','bone_58','bone_59','bone_60'],
    'right_forearm':['bone_61','bone_62','bone_64'],
    'right_glove':['bone_65']+[f'bone_{x}' for x in range(66,87)],
}
SEAMS={'left_elbow':['bone_25','bone_29'],'left_forearm_wrist':['bone_29','bone_27'],
       'left_forearm_glove':['bone_29','bone_30'],
       'right_elbow':['bone_60','bone_64'],'right_forearm_wrist':['bone_64','bone_62'],
       'right_forearm_glove':['bone_64','bone_65']}


def args():
    p=argparse.ArgumentParser()
    p.add_argument('--root',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--clips',nargs='+',default=['walk','run','jump','swim'])
    return p.parse_args()


def normq_angle(a,b):
    dot=abs(sum(x*y for x,y in zip(a,b)))
    return math.degrees(2*math.acos(max(-1.0,min(1.0,dot))))


def pctl(v,q):
    if not v:return None
    v=sorted(v)
    return v[min(len(v)-1,max(0,math.ceil(q*len(v))-1))]


def load_bundle(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:return json.load(f)


def decode_png(path):
    with open(path,'rb') as f:data=f.read()
    if data[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('not PNG '+path)
    pos=8; compressed=b''; width=height=ctype=depth=None
    while pos<len(data):
        n=struct.unpack('>I',data[pos:pos+4])[0]; typ=data[pos+4:pos+8]; block=data[pos+8:pos+8+n];pos+=12+n
        if typ==b'IHDR':width,height,depth,ctype,_,_,_=struct.unpack('>IIBBBBB',block)
        elif typ==b'IDAT':compressed+=block
        elif typ==b'IEND':break
    if depth!=8 or ctype not in (2,6):raise ValueError(f'unsupported PNG type/depth {ctype}/{depth} {path}')
    channels=4 if ctype==6 else 3; bpp=channels; stride=width*bpp
    raw=zlib.decompress(compressed); rows=[]; prev=bytearray(stride); off=0
    for _ in range(height):
        filt=raw[off]; off+=1; scan=bytearray(raw[off:off+stride]);off+=stride
        for i in range(stride):
            left=scan[i-bpp] if i>=bpp else 0; up=prev[i]; ul=prev[i-bpp] if i>=bpp else 0
            if filt==1:scan[i]=(scan[i]+left)&255
            elif filt==2:scan[i]=(scan[i]+up)&255
            elif filt==3:scan[i]=(scan[i]+((left+up)//2))&255
            elif filt==4:
                p=left+up-ul; pa=abs(p-left);pb=abs(p-up);pc=abs(p-ul)
                pr=left if pa<=pb and pa<=pc else (up if pb<=pc else ul)
                scan[i]=(scan[i]+pr)&255
            elif filt!=0:raise ValueError('unknown PNG filter')
        if channels==3:
            rgba=bytearray(width*4)
            for i in range(width):rgba[4*i:4*i+3]=scan[3*i:3*i+3];rgba[4*i+3]=255
            scan=rgba
        rows.append(scan);prev=bytearray(scan)
    return width,height,rows


def png_chunk(kind,payload):
    return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)


def write_png(path,width,height,rows):
    raw=b''.join(b'\x00'+bytes(row) for row in rows)
    data=b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+png_chunk(b'IDAT',zlib.compress(raw,6))+png_chunk(b'IEND',b'')
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,'wb') as f:f.write(data)


def assemble_sheet(ref,hyb,clip,view,out):
    keys=['rest']+[k for k in ref['samples'] if k!='rest']
    # Find view files indexed by (sample key, view) for each candidate.
    def lookup(bundle):
        x={}
        for item in bundle['render_files']:
            stem=os.path.basename(item['path'])
            viewname=item['view']
            sample='rest' if stem.startswith('neutral_') else stem.split('_'+viewname+'.png')[0].removeprefix('frame_').replace('_','.')
            # Blender outputs exact integer times with .0 in filenames.
            if sample!='rest':sample=str(float(sample))
            x[(sample,viewname)]=item['path']
        return x
    rpaths,hpaths=lookup(ref),lookup(hyb)
    first=next(iter(rpaths.values()));w,h,_=decode_png(first)
    rows=[]
    for key in keys:
        rp=rpaths.get((key,view));hp=hpaths.get((key,view))
        if not rp or not hp:continue
        _,_,a=decode_png(rp);_,_,b=decode_png(hp)
        for y in range(h):rows.append(bytearray(a[y]+b[y]))
    path=os.path.join(out,f'{clip}_{view}_contact.png')
    write_png(path,w*2,h*(len(rows)//h),rows)
    return {'path':path,'row_order':keys,'columns':['literal/reference','hybrid'],'view':view}


def mask_iou(a,b):
    # Alpha silhouettes; transparent background is enabled in the diagnostic renderer.
    intersection=union=0
    for ra,rb in zip(a,b):
        for i in range(3,len(ra),4):
            x=ra[i]>8;y=rb[i]>8
            intersection+=x and y;union+=x or y
    return intersection/union if union else 1.0


def distinct_weighted_seam(sample,bundle,pair):
    """P95 nearest distance between non-overlapping weighted vertex sets."""
    clouds={pair[0]:[],pair[1]:[]}
    for mi,mesh in enumerate(sample['meshes']):
        gi=bundle['group_indices'][mesh['name']]
        pts=mesh['points']
        for group in pair:
            clouds[group].extend(((mi,i,pts[i]) for i in gi.get(group,[]) if i<len(pts)))
    akeys={(m,i) for m,i,_ in clouds[pair[0]]}
    bkeys={(m,i) for m,i,_ in clouds[pair[1]]}
    shared=akeys & bkeys
    a=[p for m,i,p in clouds[pair[0]] if (m,i) not in shared]
    b=[p for m,i,p in clouds[pair[1]] if (m,i) not in shared]
    if not a or not b:return None
    ds=[]
    for p in a:
        ds.append(math.sqrt(min(sum((p[k]-q[k])**2 for k in range(3)) for q in b)))
    return pctl(ds,.95)


def metrics(ref,hyb):
    out=[]
    for key,rs in ref['samples'].items():
        hs=hyb['samples'][key]
        row={'sample':key,'regions':{},'joints':{},'seams':{},'wrist_relative_orientation_deg':{},'silhouette_iou':{}}
        for ri,(rm,hm) in enumerate(zip(rs['meshes'],hs['meshes'])):
            rp,hp=rm['points'],hm['points']
            if len(rp)!=len(hp):
                row['regions'][f'mesh{ri}']={'topology_match':False,'reference_vertices':len(rp),'hybrid_vertices':len(hp)}
                continue
            deltas=[math.sqrt(sum((a[k]-b[k])**2 for k in range(3))) for a,b in zip(rp,hp)]
            row['regions'][f'mesh{ri}']={'topology_match':True,'vertex_rms':math.sqrt(sum(x*x for x in deltas)/len(deltas)),
                'vertex_p95':pctl(deltas,.95),'vertex_max':max(deltas)}
            rgi=ref['group_indices'][rm['name']];hgi=hyb['group_indices'][hm['name']]
            for region,bones in REGIONS.items():
                ids=set(i for bone in bones for i in rgi.get(bone,[])) & set(i for bone in bones for i in hgi.get(bone,[]))
                if not ids:continue
                dv=[deltas[i] for i in ids]
                entry=row['regions'].setdefault(region,{'count':0,'sum_sq':0.0,'deltas':[],'centroid_delta_sum':[0.0,0.0,0.0]})
                entry['count']+=len(ids);entry['sum_sq']+=sum(v*v for v in dv);entry['deltas'].extend(dv)
                for axis in range(3):
                    dr=sum(hp[i][axis]-rp[i][axis] for i in ids)
                    entry['centroid_delta_sum'][axis]+=dr
        for region,item in list(row['regions'].items()):
            if 'deltas' in item:
                n=item.pop('count');ss=item.pop('sum_sq');d=item.pop('deltas');cd=item.pop('centroid_delta_sum')
                item.update({'vertices':n,'rms':math.sqrt(ss/n),'p95':pctl(d,.95),'max':max(d),
                             'centroid_delta': [x/n for x in cd]})
        for bone in ['bone_25','bone_26','bone_29','bone_27','bone_30']:
            if bone in rs['bones'] and bone in hs['bones']:
                a=rs['bones'][bone]['head'];b=hs['bones'][bone]['head']
                row['joints'][bone+'_head_delta']=math.sqrt(sum((a[i]-b[i])**2 for i in range(3)))
        for side in ['left','right']:
            rrel=rs['bones'].get('relative_hand_'+side,{})
            hrel=hs['bones'].get('relative_hand_'+side,{})
            for forearm in set(rrel)&set(hrel):
                row['wrist_relative_orientation_deg'][side+'_'+forearm]=normq_angle(rrel[forearm],hrel[forearm])
        for seam,pair in SEAMS.items():
            a=distinct_weighted_seam(rs,ref,pair);b=distinct_weighted_seam(hs,hyb,pair)
            row['seams'][seam]={'groups':pair,'reference_p95':a,'hybrid_p95':b,
                                'delta':(b-a) if a is not None and b is not None else None}
        for view in ['front','side','three_quarter']:
            rp=next(x['path'] for x in ref['render_files'] if x['view']==view and
                    (('rest' in key and os.path.basename(x['path']).startswith('neutral_')) or
                     ('rest' not in key and os.path.basename(x['path']).startswith('frame_'+key.replace('.','_')))))
            hp=next(x['path'] for x in hyb['render_files'] if x['view']==view and
                    (('rest' in key and os.path.basename(x['path']).startswith('neutral_')) or
                     ('rest' not in key and os.path.basename(x['path']).startswith('frame_'+key.replace('.','_')))))
            row['silhouette_iou'][view]=mask_iou(decode_png(rp)[2],decode_png(hp)[2])
        out.append(row)
    return out


def main():
    a=args();os.makedirs(a.output,exist_ok=True)
    clips={}
    for clip in a.clips:
        r=load_bundle(os.path.join(a.root,clip,'reference','data.json.gz'))
        h=load_bundle(os.path.join(a.root,clip,'hybrid','data.json.gz'))
        sheets=[assemble_sheet(r,h,clip,v,a.output) for v in ['front','side','three_quarter']]
        clips[clip]={'reference_blend':r['blend'],'hybrid_blend':h['blend'],'frames':[x for x in r['samples'] if x!='rest'],
                     'neutral_rest_included':'rest' in r['samples'],'camera':r['camera'],
                     'sheets':sheets,'metrics':metrics(r,h)}
    report={'schema':'mg1.hybrid-literal-comparison.v1','root':a.root,'clips':clips}
    path=os.path.join(a.output,'comparison.json')
    with open(path,'w',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(path)


if __name__=='__main__':main()
