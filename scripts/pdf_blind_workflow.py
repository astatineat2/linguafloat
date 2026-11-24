"""One entry point for a local English-to-Chinese PDF quality run. Offline by default."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
from fnmatch import fnmatchcase
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pdf_blind_audit import inspect
from pdf_blind_ledger import LABEL, publish

QUESTIONS = {
    'prose-needs-review':'这段是否属于应译正文？在译文侧是否完整出现？',
    'unchanged-short-cell':'这是应译的短词，还是应保留的标识？',
    'unchanged-table-line':'这行是应译的表格文字吗？', 'possibly-empty-cell':'原单元格的内容还在对应格内吗？',
    'panel-copy':'左右独立文件的差异只是空白，还是实际复制丢字或乱序？',
    'table-geometry-needs-review':'这里确实有独立表格吗？译文的行列是否完整？',
    'no-copy-layer':'这是预期的空白或插图，还是应能复制的文字？',
    'link-hotspot-count':'拆分或合并热区后，原来可点击的位置还能使用吗？',
    'split-page':'内容移到续页后，原页内容是否完整、顺序是否正确？',
    'geometry-needs-review':'页面旋转或裁切后，译文是否还在正确的单元格或栏内？',
}


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def dump(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf8'); temp.replace(path)


def differences(old, new):
    def keys(items):
        return Counter((f['rule'],f['page'],str(f['evidence'])) for f in items)
    before,after = keys(old),keys(new)
    def pack(counts):
        return [dict(rule=r,page=p,evidence=e,count=n) for (r,p,e),n in sorted(counts.items())]
    return dict(new=pack(after-before),absent=pack(before-after),repeated=pack(before&after))


def link(path, root):
    try: return quote(Path(os.path.relpath(path, root)).as_posix(),safe='/')
    except ValueError: return Path(path).as_uri()


def inputs(arg, outputs):
    if arg.is_file():
        records = json.loads(arg.read_text('utf-8-sig'))['papers']
        if not isinstance(records,list): raise ValueError('清单 papers 必须是列表')
        return [( (arg.parent/r['source']).resolve(), [(arg.parent/r['pair_dir']).resolve()] if r.get('pair_dir') else [], r.get('sha256')) for r in records]
    if not arg.is_dir(): raise ValueError(f'找不到语料目录：{arg}')
    index = defaultdict(set)
    for path in (outputs or arg).rglob('*.pdf'):
        for suffix in ('.双语.pdf','.译文.pdf'):
            if path.name.endswith(suffix): index[path.name[:-len(suffix)]].add(path.parent)
    return [(p, sorted(index.get(p.stem,())), None) for p in sorted(arg.glob('*.pdf'))
            if not p.name.endswith(('.双语.pdf','.译文.pdf'))]


def reports(dest, run, *, final=True, ledger_root=None):
    cases = run['cases']; counts = Counter(c['status'] for c in cases)
    lines = ['# PDF 质量复查', '', f"本轮 {len(cases)} 篇；"+'，'.join(f'{LABEL[s]} {n} 篇' for s,n in counts.items())+'。',
             f"本次发送 {run['requests']} 个翻译请求；供应商报告 {run['tokens']} Token。耗时 {run['seconds']:.1f} 秒。",
             '复查已有 PDF 不代表当前软件重新生成过；本次规则未报警不代表逐句翻译正确。', '',
             '| 论文 / 双语 PDF | 模式 | 页数 | 结果 | 本轮请求 / 已报告 Token / 秒 |','|---|---|---:|---|---|']
    grouped = defaultdict(list); detail = []
    for c in cases:
        name = Path(c['source']).stem.replace('|',' ')
        item = f"[{name}]({link(c['dual'],dest)})" if c.get('dual') else name
        lines.append(f"| {item} | {c['mode']} | {c.get('pages','?')} | {LABEL[c['status']]} | {c['requests']} / {c['tokens']} / {c['seconds']:.1f} |")
        if 'difference' in c:
            d=c['difference']; detail.append(f"- {name} 同原文对照：新增 {sum(x['count'] for x in d['new'])}，仍报 {sum(x['count'] for x in d['repeated'])}，未再报警 {sum(x['count'] for x in d['absent'])}；后者不自动算已修。")
        for f in c['findings']:
            if f['severity']=='review': grouped[f['rule']].append((c,f))
        for rule in sorted({f['rule'] for f in c['findings'] if f['severity']!='review'}):
            hits = [f for f in c['findings'] if f['rule']==rule]; pages = sorted({str(f['page']) for f in hits if f['page']},key=int)
            detail.append(f"- {name}：`{rule}` ×{len(hits)}，页 {', '.join(pages) or '文档级'}。{hits[0]['evidence']}")
    lines += ['', '## 已拦下或未验证', *(detail or ['无。']), '', '[待人工核验](待人工核验/请看这里.md) · [机器记录、坐标与样本指纹](run.json)']
    (dest/'本轮日志.md').write_text('\n'.join(lines)+'\n','utf8')
    review = ['# 待人工核验', '', '同类疑点合并列出；确定的错误和缺文件不交给你判断。']
    for rule, entries in grouped.items():
        review += ['', '## '+QUESTIONS.get(rule,rule)]
        by_case = defaultdict(list)
        for c,f in entries: by_case[c['source']].append((c,f))
        for src, hits in by_case.items():
            c,f = hits[0]; pages = sorted({i['page'] for _,i in hits}); snippet = f['evidence']
            review.append(f"- [{Path(src).stem} 原文]({link(src,dest/'待人工核验')}) / [双语]({link(c['dual'],dest/'待人工核验')})，页 {', '.join(map(str,pages))}。例：第 {f['page']} 页，{snippet}" + (f"；同位置译文：{f['target'] or '未提取到文字'}" if 'target' in f else ''))
    if not grouped: review += ['', '本轮无待人工核验项。']
    (dest/'待人工核验/请看这里.md').write_text('\n'.join(review)+'\n','utf8')
    dump(dest/'run.json',run)
    if final:
        return publish(ledger_root or dest.parent, dest, run)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path, help='原文 PDF 文件夹，或固定原文、结果目录与摘要的 JSON 清单')
    parser.add_argument('--outputs',type=Path, help='已有输出目录；同名候选不自动择新')
    parser.add_argument('--out',type=Path,help='新的结果目录，不能覆盖旧结果')
    parser.add_argument('--match',action='append',help='按原文文件名（无扩展名）筛选，可重复，支持 *')
    parser.add_argument('--generate',action='store_true',help='用当前源码对缺少输出的论文执行翻译')
    parser.add_argument('--rebuild',action='store_true',help='对选中论文强制用当前源码另存，再复查')
    parser.add_argument('--requests',type=int,default=0,help='整个批次真实请求上限，默认不允许')
    parser.add_argument('--reserve',type=int,default=0,help='批次保守 Token 预留上限（请求字节数加输出上限）')
    parser.add_argument('--cache',type=Path,help='额外优先复用的隔离任务缓存数据库，只读')
    parser.add_argument('--previous',type=Path,help='上一次 run.json；同一检查器、同一原文才比较')
    parser.add_argument('--ledger-root',type=Path,help='累计总表目录（默认与批次目录同级）')
    parser.add_argument('--diagnostic',action='store_true',help='反例、摘页或自拟诊断，不计入累计总表')
    args = parser.parse_args(argv)
    if args.requests<0 or args.reserve<0 or bool(args.reserve)!=bool(args.requests) or (args.requests and not (args.generate or args.rebuild)):
        parser.error('真实翻译必须显式指定 --generate 或 --rebuild 和两个正预算')
    if args.cache and not args.cache.is_file(): parser.error('指定的缓存数据库不存在')
    source = args.input.resolve()
    if args.outputs and (source.is_file() or not args.outputs.is_dir()): parser.error('--outputs 仅接受目录输入和有效的输出库')
    selected = [(p,d,h) for p,d,h in inputs(source,args.outputs.resolve() if args.outputs else None) if not args.match or any(fnmatchcase(p.stem,x) for x in args.match)]
    if not selected or len({p for p,_,_ in selected}) != len(selected): parser.error('选样为空，或清单含重复原文路径')
    rule_hash = hashlib.sha256(''.join(digest(Path(__file__).with_name(n)) for n in ('check_pdf_sample.py','pdf_blind_audit.py')).encode()).hexdigest()
    old_cases = {}
    if args.previous:
        old=json.loads(args.previous.read_text('utf8'))
        if old.get('rules_sha256')!=rule_hash: parser.error('检查器已变化，请用当前规则先复查旧文件再比较')
        old_cases={c['sha256']:c for c in old['cases'] if c['status']!='unverified'}
    library_root = next((p for p in source.parents if p.name=='测试结果'),None)
    if library_root is None and (source.parent/'测试结果').is_dir(): library_root=source.parent/'测试结果'
    default_root = library_root or source.parent
    dest = (args.out or default_root/('PDF复查-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))).resolve()
    dest.mkdir(parents=True,exist_ok=False); (dest/'待人工核验').mkdir()
    start = time.monotonic()
    run = dict(cases=[],requests=0,tokens=0,reserved=0,rules_sha256=rule_hash,request_limit=args.requests,reservation_limit=args.reserve,network_retries=0)
    dump(dest/'selection.json',[dict(source=str(p),expected_sha256=h,pair_dirs=list(map(str,d))) for p,d,h in selected])
    for i,(p,dirs,h) in enumerate(selected):
        began=time.monotonic(); c=dict(source=str(p),findings=[],requests=0,tokens=0,reserved=0,mode='已有文件离线复查')
        try:
            c['sha256']=digest(p)
            if h and c['sha256']!=h: raise ValueError('原文件 SHA-256 与清单不一致')
            incomplete = not dirs or (len(dirs)==1 and not all((dirs[0]/(p.stem+s)).is_file() for s in ('.译文.pdf','.双语.pdf')))
            if args.rebuild or (args.generate and incomplete) or (source.is_dir() and args.outputs is None and not dirs):
                from pdf_blind_generate import generate
                c['mode']='源码重新生成'; folder=dest/f'job-{i+1:03d}'
                previous_level=logging.root.manager.disable
                try:
                    logging.disable(logging.CRITICAL); mono,dual=generate(p,folder,args,run,c)
                finally: logging.disable(previous_level)
            else:
                if len(dirs)!=1: raise ValueError('没有配对输出' if not dirs else '存在多个输出目录；请用清单固定要验的版本')
                mono=dirs[0]/(p.stem+'.译文.pdf'); dual=dirs[0]/(p.stem+'.双语.pdf')
            if not mono.is_file() or not dual.is_file(): raise ValueError('同一结果目录内缺少单语或双语文件')
            c.update(mono=str(mono),dual=str(dual),mono_sha256=digest(mono),dual_sha256=digest(dual))
            c['pages'], c['findings'] = inspect(p,mono,dual)
            c['status']=next((s for s in ('fail','hold','review') if any(f['severity']==s for f in c['findings'])),'pass')
            if c['sha256'] in old_cases:
                c['difference']=differences(old_cases[c['sha256']]['findings'],c['findings'])
        except Exception as exc:
            c['status']='unverified'; c['findings'].append(dict(rule='not-verified',severity='hold',page=0,evidence=str(exc) if isinstance(exc,(ValueError,FileNotFoundError)) else type(exc).__name__))
        c['seconds']=round(time.monotonic()-began,2); run['cases'].append(c)
        for key in ('requests','tokens','reserved'): run[key]+=c[key]
        run['seconds']=round(time.monotonic()-start,2)
        table=reports(dest,run,final=not args.diagnostic and i+1==len(selected),ledger_root=args.ledger_root or library_root)
        print(f"{i+1}/{len(selected)} {p.stem}: {c['status']}",flush=True)
    print(table or dest,flush=True); return int(any(c['status']!='pass' for c in run['cases']))


if __name__=='__main__': raise SystemExit(main())
