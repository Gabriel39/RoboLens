"""Build bilingual, self-contained SVG diagrams for the notebook presentation."""
from pathlib import Path
from html import escape

INK='#142c43'; MUTED='#60758a'; TEAL='#078b80'; BLUE='#376cf0'; GOLD='#d89536'
ROOT=Path(__file__).parent/'assets'

def text(x,y,s,size=18,color=INK,weight=400,anchor='start'):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(s))}</text>'
def rect(x,y,w,h,fill='#fff',stroke='none',r=14):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}"/>'
def line(x,y,xx,yy,color=MUTED,width=2,dash=''):
    return f'<path d="M{x},{y} L{xx},{yy}" fill="none" stroke="{color}" stroke-width="{width}" stroke-dasharray="{dash}"/>'
def circle(x,y,r,fill,stroke='none'):
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" stroke="{stroke}"/>'
def arrow(x,y,xx,yy):
    return line(x,y,xx,yy,'#93a6b5',2)+f'<path d="M{xx-8},{yy-5} L{xx},{yy} L{xx-8},{yy+5}" fill="none" stroke="#93a6b5" stroke-width="2"/>'
def label(x,y,n,title,sub):
    return circle(x+14,y-7,14,'#e9effa')+text(x+14,y-2,n,12,BLUE,700,'middle')+text(x+40,y,title,21,INK,700)+text(x+40,y+28,sub,14,MUTED)
def image(x,y,w=170,h=105,accent=TEAL):
    return (rect(x,y,w,h,'#e8f0f2',r=10)+rect(x+7,y+7,w-14,h-14,'#f7fafb',r=6)
      +f'<path d="M{x+12},{y+h-18} L{x+w*.36},{y+h*.40} L{x+w*.57},{y+h*.65} L{x+w*.75},{y+h*.36} L{x+w-12},{y+h-18} Z" fill="{accent}" opacity=".55"/>'
      +circle(x+w*.74,y+24,9,GOLD)+line(x+w*.20,y+h-12,x+w*.32,y+35,INK,7)+circle(x+w*.32,y+35,6,INK))
def camera(x,y,s=1):
    return f'<g transform="translate({x},{y}) scale({s})">'+rect(0,10,95,65,'#edf4fa',BLUE,12)+rect(16,0,30,15,BLUE,r=4)+circle(48,42,20,'#fff',BLUE)+circle(48,42,11,TEAL)+'</g>'
def table(x,y,w=180,h=112):
    z=rect(x,y,w,h,'#fff','#c6d4e1',10)+rect(x,y,w,26,'#e7f0fc',r=9)
    for j in range(1,4): z+=line(x,y+26+j*(h-26)/4,x+w,y+26+j*(h-26)/4,'#e3eaf1',1)
    for k in [0.3,0.65]: z+=line(x+w*k,y+26,x+w*k,y+h,'#e3eaf1',1)
    return z

def graph(x,y):
    points=[(12,50),(55,10),(82,72),(135,22),(155,100),(45,123),(112,138)]
    z=''
    for a,b in [(0,1),(0,2),(0,5),(1,3),(2,3),(2,4),(2,5),(2,6),(4,6)]:
        z+=line(x+points[a][0],y+points[a][1],x+points[b][0],y+points[b][1],'#b9cfdf')
    for i,(px,py) in enumerate(points):z+=circle(x+px,y+py,11,TEAL if i==2 else '#83a6ec')
    return z

def doc(x,y,color=TEAL):
    z=rect(x,y,100,128,'#fff','#c6d4e1',10)+rect(x+14,y+17,31,8,color,r=4)
    for i,w in enumerate([70,55,65,48]):z+=rect(x+14,y+42+i*16,w,5,'#cfdae4',r=2)
    return z

DATA={
'zh':[
('00_overview','机器人数据如何变成训练候选？','流程总览 · 示例数据：DROID-100', ['采集','向量化','存储与查询','人工复核','训练候选'],'一个数据底座，贯穿检索、复核与选样。'),
('01_source','三路画面，同步一条动作轨迹','源数据结构', ['三路相机','同步状态 / 动作','一次 episode'], '100 集 · 300 段视频 · 32,212 个状态 / 动作时间步'),
('02_embedding','让画面可以被比较','视觉特征提取', ['采样画面','SigLIP2','768 维向量'], '状态与动作保留原值；向量表达单帧视觉特征。'),
('03_lance','原始数据和检索索引一起保存','对象存储 · S3 / OSS', ['结构化记录','Lance 数据集','向量索引'], '保存原视频、状态 / 动作与向量；导出完整后再检索。'),
('04_catalog','Doris 连接同一份 Lance 数据','数据访问路径', ['S3 / OSS','Doris Catalog','分析内表'], '原始数据留在对象存储；命中、复核与训练登记保存在 Doris。'),
('05_search','一张参考图，找到相似场景','以图搜图 · 示意画面', ['参考图片','近似向量检索','每集一张命中图'], '同相机、同模型，排除参考集；相似不等于失败。'),
('06_review','看完整过程，再给出判断','人工复核 · 示意画面', ['候选片段','视频 + 动作','人工标签'], '记录成败、原因与时间范围；不能仅凭单帧判断。'),
('07_analysis','先看覆盖，再看失败分布','统计流程 · 示意，不是实测结果', ['已复核样本','有效统计范围','失败分布'], '只有质量可用、任务匹配且成败已知的复核样本进入分母。'),
('08_training','把候选样本分配到合适用途','训练选样流程', ['过滤','去重','分配用途'], '排除已训练与非训练集；成功用于模仿学习，失败用于诊断或 critic。'),
],
'en':[
('00_overview','From robot experience to training candidates','WORKFLOW / DROID-100', ['Capture','Embed','Store & query','Review','Select'],'One data foundation for retrieval, review and selection.'),
('01_source','Three views, one synchronized trajectory','SOURCE STRUCTURE', ['Three cameras','State / action','One episode'],'100 episodes / 300 videos / 32,212 synchronized steps'),
('02_embedding','Make images comparable','VISUAL FEATURES', ['Sampled frames','SigLIP2','768-D vector'],'State and action stay unchanged; embeddings describe individual images.'),
('03_lance','Preserve data and index it for retrieval','OBJECT STORAGE / S3 / OSS',['Structured records','Lance datasets','Vector index'],'Keep original video, state/action and vectors; query only complete exports.'),
('04_catalog','Doris reads the same Lance export','DATA ACCESS PATH',['S3 / OSS','Doris Catalog','Analysis tables'],'Source data stays in object storage; Doris stores hits, reviews and policy.'),
('05_search','One reference, similar scenes','IMAGE SEARCH / SCHEMATIC',['Reference image','Vector search','One hit per episode'],'Same camera and model; exclude the reference episode. Similarity is not failure.'),
('06_review','Watch the attempt before judging it','HUMAN REVIEW / SCHEMATIC',['Candidate clip','Video + actions','Human labels'],'Record outcome, cause and time bounds; one still image is not enough.'),
('07_analysis','Check coverage before failure rates','STATISTICAL FLOW / ILLUSTRATION ONLY',['Reviewed samples','Eligible attempts','Failure distribution'],'Denominator: reviewed, usable, task-matched attempts with known outcomes.'),
('08_training','Match each candidate to its training use','SAMPLE SELECTION',['Filter','Deduplicate','Assign use'],'Exclude trained and held-out data. Success: imitation. Failure: diagnosis / critic.'),
]}

def render(index,lang,entry):
    name,title,kicker,labels,foot=entry; zh=lang=='zh'
    out=rect(0,0,1120,410,'#f1f5f9',r=24)
    out+=text(30,32,kicker,12,MUTED,600)
    if index==0:
        for i,title in enumerate(labels):
            x=34+i*219
            out+=rect(x,78,184,228,'#fff',r=18)+text(x+20,111,f'0{i+1}',13,TEAL,700)
            if i==0:out+=camera(x+40,139)
            elif i==1:
                for a in range(6):out+=rect(x+33+a*20,158+(a%3)*10,12,74-(a%3)*10,BLUE,r=5)
            elif i==2:out+=table(x+24,143,138,94)
            elif i==3:out+=circle(x+90,175,33,'#e5f5ef')+f'<path d="M{x+73},176 l12,12 l24,-28" fill="none" stroke="{TEAL}" stroke-width="5"/>'
            else:out+=doc(x+45,131)
            out+=text(x+92,279,title,19,INK,600,'middle')
            if i<4:out+=arrow(x+190,190,x+211,190)
        out+=text(32,363,foot,19,INK,600)
    else:
        subs={1:['RGB / MP4','7 + 7 dimensions','time-aligned'],2:['RGB','image encoder','unit-normalized'],3:['frames / media','columnar + binary','cosine'],4:['Lance','SQL','hits / labels'],5:['query','top_k 2,000','up to 50 episodes'],6:['timeline','inspect','success / failure / unknown'],7:['coverage','denominator','by object / scene'],8:['eligibility','scene groups','training purpose']}[index]
        for j in range(3):out+=label(30+j*370,76,f'{j+1:02}',labels[j],subs[j])
        if index==1:
            for i in range(3):out+=image(36+i*33,143+i*24,178,103,[TEAL,BLUE,'#9274c0'][i])
            for i in range(2):
                out+=text(419,158+i*62,['state','action'][i],12,MUTED)
                for j in range(7):out+=rect(419+j*36,174+i*62,26,24,[TEAL,BLUE][i],r=5)
            out+=rect(795,145,272,146,'#fff',r=15)
            for i in range(3):
                out+=line(819,177+i*39,1043,177+i*39,'#cbd9e5',3)
                for j in range(6):out+=circle(828+j*39,177+i*39,5,[TEAL,BLUE,GOLD][i])
            out+=line(927,157,927,279,INK,2,'4 4')
        elif index==2:
            out+=image(64,150,222,133)
            for i in range(4):
                for j in range(5):out+=rect(435+j*41,150+i*36,29,25,TEAL if (i+j)%3==0 else '#b8cff9',r=6)
            for j,h in enumerate([43,101,66,118,54,87,38,108,70,93]):out+=rect(807+j*24,286-h,13,h,TEAL if j%2 else BLUE,r=5)
            out+=arrow(314,218,383,218)+arrow(694,218,764,218)
        elif index==3:
            out+=table(67,144,218,145)+text(90,163,'episode | frame | vector',12,BLUE,600)
            out+=rect(424,141,246,155,'#e2f0f2',r=24)
            for i,t in enumerate(['frames.lance','media.lance','frame_embeddings.lance']):out+=rect(444,158+i*42,205,32,'#fff',r=8)+text(456,179+i*42,t,14,INK,500)
            out+=graph(828,143)+arrow(309,219,398,219)+arrow(699,219,782,219)
        elif index==4:
            out+=table(74,152,207,131)+text(101,173,'Lance / export manifest',12,BLUE,600)
            out+=rect(426,151,232,132,INK,r=20)+text(542,210,'DORIS',34,'#fff',700,'middle')+text(542,247,'catalog.database.table',13,'#a9c1d6',400,'middle')
            out+=doc(800,148,BLUE)+doc(927,148,TEAL)
            out+=arrow(302,218,402,218)+arrow(680,218,775,218)
        elif index==5:
            out+=image(60,143,227,141)+rect(60,295,98,23,'#e2f1ec',r=10)+text(109,311,'REFERENCE',10,TEAL,700,'middle')
            out+=graph(459,145)+circle(541,217,39,'none',TEAL)
            out+=image(792,150,125,82,BLUE)+image(937,150,125,82,TEAL)+image(865,247,125,65,'#9274c0')
            out+=arrow(314,215,404,215)+arrow(681,215,764,215)
        elif index==6:
            for i in range(3):out+=image(39+i*89,174,80,62,[BLUE,TEAL,BLUE][i])
            out+=line(43,268,300,268,'#b9ccd9',3)+circle(160,268,8,TEAL)+text(166,301,'t',14,MUTED)
            out+=image(440,144,216,118)+line(442,295,655,295,BLUE,3)
            out+=f'<path d="M442,294 l20,-17 l23,24 l28,-41 l35,22 l29,-14 l28,31 l50,-17" fill="none" stroke="{TEAL}" stroke-width="3"/>'
            for i,(t,c) in enumerate(zip(['SUCCESS','FAILURE','UNKNOWN'],[TEAL,GOLD,MUTED])):out+=rect(803,147+i*53,239,39,'#fff',r=10)+circle(824,166+i*53,6,c)+text(846,172+i*53,t,14,c,600)
            out+=arrow(324,218,407,218)+arrow(686,218,772,218)
        elif index==7:
            for i in range(4):
                for j in range(6):out+=circle(78+j*34,163+i*35,10,TEAL if i+j<5 else '#c9d6e2')
            out+=f'<path d="M442,147 H659 L592,224 V289 H512 V224 Z" fill="#deedf2" stroke="#aac5d4" stroke-width="2"/>'
            out+=text(551,198,'quality',14,INK,500,'middle')+text(551,220,'task + outcome',12,MUTED,400,'middle')
            out+=line(806,287,1051,287,'#bbccd9')
            for j,h in enumerate([85,132,56,106]):out+=rect(825+j*53,286-h,32,h,[BLUE,TEAL,GOLD,'#a3b9d7'][j],r=6)
            out+=arrow(315,218,401,218)+arrow(687,218,766,218)
        elif index==8:
            for i,(t,ok) in enumerate([('known history',True),('train split',True),('usable clip',True)]):
                out+=rect(47,149+i*49,255,36,'#fff',r=10)+circle(68,167+i*49,8,TEAL)+text(86,173+i*49,t,14,INK,500)
            out+=doc(459,158,BLUE)+doc(487,148,BLUE)+doc(515,138,TEAL)
            for i,(t,sub,c) in enumerate([('SUCCESS','Imitation / BC',TEAL),('FAILURE','Diagnosis / critic',GOLD)]):
                out+=rect(796,142+i*87,269,74,'#fff',r=13)+rect(796,142+i*87,5,74,c,r=2)+text(817,168+i*87,t,12,c,700)+text(817,194+i*87,sub,19,INK,600)
            out+=arrow(327,219,403,219)+arrow(686,219,769,219)
        out+=line(30,348,1090,348,'#d5e1eb',1)+text(30,380,foot,16,MUTED)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="410" viewBox="0 0 1120 410" role="img" aria-labelledby="title desc">'
      f'<title id="title">{escape(title)}</title><desc id="desc">{escape(kicker+". "+foot)}</desc>'
      '<g font-family="Noto Sans CJK SC,Microsoft YaHei,PingFang SC,Arial,sans-serif">'+out+'</g></svg>')

def main():
    for lang, entries in DATA.items():
        folder=ROOT/('en' if lang=='en' else '')
        folder.mkdir(parents=True,exist_ok=True)
        for i,entry in enumerate(entries):(folder/(entry[0]+'.svg')).write_text(render(i,lang,entry))
if __name__=='__main__':main()
