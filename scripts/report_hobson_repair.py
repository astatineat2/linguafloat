"""Render the actual worker output and publish a traceable local audit."""
import json, shutil
from pathlib import Path
import pymupdf as f
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/hobson-v0.9.2'
task=json.loads((OUT/'task-identity.json').read_text(encoding='utf-8'))
evidence=json.loads((OUT/'repair-evidence.json').read_text(encoding='utf-8'))
final=next((OUT/'pipeline').rglob('*.双语.pdf'))
dest=ROOT/'output/pdf/Hobson-v0.9.2-保留版式修复-日文双语.pdf'
dest.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2(final,dest)
report=json.loads(next((OUT/'pipeline').rglob('layout-report.json')).read_text(encoding='utf-8'))
rows={r['id']:r for r in report['graphics_regions']}
with f.open(final) as pdf,f.open(task['details']['outputs'][0]) as old:
    assert len(pdf)==len(old)==9
    assert all(p.rect==old[i].rect for i,p in enumerate(pdf))
    for n in (2,3,4,5,6,8):
        pdf[n].get_pixmap(matrix=f.Matrix(1.5,1.5),clip=f.Rect(632,26,1244,818)).save(OUT/f'pipeline-p{n+1}.png')
    clip=f.Rect(130,127,476,255)+(632,26,632,26)
    old[3].get_pixmap(matrix=f.Matrix(3,3),clip=clip).save(OUT/'pipeline-table-before.png')
    pdf[3].get_pixmap(matrix=f.Matrix(3,3),clip=clip).save(OUT/'pipeline-table-after.png')
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',24)
before=Image.open(OUT/'pipeline-table-before.png').convert('RGB')
after=Image.open(OUT/'pipeline-table-after.png').convert('RGB')
canvas=Image.new('RGB',(before.width+32,before.height+after.height+136),'#f6f8fb')
draw=ImageDraw.Draw(canvas)
draw.text((16,10),'修改前：原任务真实日文输出，三个表头未排入',font=font,fill='#253245')
canvas.paste(before,(16,48))
draw.text((16,before.height+66),'修改后：复用真实译文缓存，统一 7.99pt，保留单位和数据',font=font,fill='#253245')
canvas.paste(after,(16,before.height+104))
canvas.save(OUT/'comparison.png')
lines=['# Hobson 2022 本次十处问题核查','','任务：`'+task['id']+'`；开始时间：2026-11-04 16:17:48。原任务目标为日文，当前界面的中文设置不改变历史任务。',
       '', '完整九页走实际 document_worker，仅重新排版，禁止 HTTP；22.075 秒，译后中间数据命中，三处表头片段命中，零新 Token。原始输出未覆盖。',
       '', '| 页码 | 区域编号 | 原识别 | 原因与处理 | 结果 |','|---|---|---|---|---|']
for item in evidence['ten_issues']:
    new=rows[item['id']]
    assert new['status']==item['after_status']
    why=('表格矢量轮廓及多层表头被 OCR 切成伪行列，可用高度被压为约 5.4pt。现识别表线和单元格，保留单位层，共享首行基线，长表头允许换行。'
         if new['status']=='translated' else new['reason'])
    result=(new['translation']+'；'+str(new['size'])+'pt') if new['status']=='translated' else '保留原始科学字形/图形；不计为已翻译'
    lines.append(f"| {item['page']} | `{item['id']}` | {item['source']} | {why} | {result} |")
lines += ['', '## 范围和限制', '',
          '以上针对原报告的十个独立区域，不是十页。三处为实际表头漏译，七处为公式、变量、波形或人像标记的误报。没有用取消完整性检查来清除告警。',
          '整份输出成功生成不等于整篇术语均经人工审定。额外发现第 5 页 Table III 未被现有图表检测器完整识别，不在这十处中；仍需专门优化检测与验证，不能宣称整篇图表全部覆盖。',
          '', '## 用户授权的三次中文短请求', '',
          '供应商：组内；模型 gpt-6.1-sol；Responses。每次输出上限 128 Token，重试为零；总三次调用，未进行额外付费补译。',
          '返回：Coil efficiency → 线圈效率；Target field → 目标领域；Target field harmonic → 目标场谐波。第二项在磁场语境不准确，应为“目标磁场”；接口成功不代表术语验证通过。中文试验未写入原任务的日文修复 PDF。',
          '供应商报告输入 13,320、输出 83 Token。未按表头字数推算账单，不虚报实际费用。',
          '', '原任务身份、逐区域诊断、实际 worker 事件和三次请求结果保存在同目录 JSON 文件；不包含密钥。']
(OUT/'十处问题对照.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(dest)
