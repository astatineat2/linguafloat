"""Single local delivery table; completed QA runs opt in, diagnostic folders do not."""
from collections import Counter, defaultdict
from pathlib import Path
import json
import os
import sqlite3
from tempfile import NamedTemporaryFile
from urllib.parse import quote

TITLE = '翻译质量测试总表.md'
LABEL = {'fail':'未通过', 'hold':'有保留记录', 'review':'待核验', 'unverified':'未验证', 'pass':'本次规则未报警'}
RULE = {'missing-link-target':'缺少链接目标', 'missing-visible-email':'缺少可见邮箱', 'worker-retained':'引擎保留', 'visible-retained':'可见未译标记', 'translated-reference-title':'参考文献题名被译', 'untranslated-table-text':'表格文字漏译', 'detached-vector-bond':'化学键分离', 'not-verified':'无法完成验证'}


def ref(path, root):
    try: return quote(Path(os.path.relpath(path, root)).as_posix(), safe='/')
    except ValueError: return Path(path).as_uri()


def publish(root, folder, run, *, opening=None):
    """Register one completed batch only once; preserve insertion order across refreshes."""
    root, folder = Path(root).resolve(), Path(folder).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root/'翻译质量测试索引.sqlite3', timeout=30) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS runs (folder TEXT NOT NULL UNIQUE, data TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS opening (hash TEXT PRIMARY KEY, requests INTEGER, tokens INTEGER, log TEXT)')
        if opening:
            db.executemany('INSERT OR REPLACE INTO opening VALUES (?,?,?,?)',opening)
        items=[]
        for c in run['cases']:
            row={k:c.get(k) for k in ('sha256','source','pages','dual','status','requests','tokens','repair_note')}
            counts=Counter(f['rule'] for f in c['findings'] if f['severity']!='review')
            row['issues']='；'.join(f'{RULE.get(k,k)} ×{v}' for k,v in counts.most_common())
            row['review']=sum(f['severity']=='review' for f in c['findings']);items.append(row)
        db.execute('INSERT INTO runs VALUES (?,?) ON CONFLICT(folder) DO UPDATE SET data=excluded.data',(str(folder),json.dumps(items,ensure_ascii=False)))
        hist=defaultdict(list)
        for d,raw in db.execute('SELECT folder,data FROM runs ORDER BY rowid'):
            for c in json.loads(raw):
                hist[c['sha256'] or ('未验证路径:'+c['source'])].append((Path(d),c))
        costs={k:(a,b,log) for k,a,b,log in db.execute('SELECT * FROM opening')}
        totals=Counter();states=Counter();rows=[]
        for key,series in sorted(hist.items(),key=lambda x:Path(x[1][-1][1]['source']).name.casefold()):
            d,c=series[-1]; state=LABEL[c['status']];states[state]+=1
            pages=next((v['pages'] for _,v in reversed(series) if v['pages'] is not None),None)
            if pages is not None: totals['pages']+=pages
            else: totals['unknown_pages']+=1
            calls,tokens,old_log=costs.get(key,(0,0,None));calls+=sum(v['requests'] for _,v in series);tokens+=sum(v['tokens'] for _,v in series);totals.update(requests=calls,tokens=tokens)
            title=Path(c['source']).stem.replace('|','\\|').replace('[','\\[').replace(']','\\]')
            paper=f"[{title}]({ref(c['dual'],root)})" if c['dual'] and Path(c['dual']).is_file() else title+'（无本次成品）'
            docs=[f"[复查记录]({ref(d/'本轮日志.md',root)})"]
            if old_log: docs.append(f'[初译记录]({ref(old_log,root)})')
            if len(series)>1: docs.append(f"[上次记录]({ref(series[-2][0]/'本轮日志.md',root)})")
            issues=c['issues'] or ('仅有待核验项' if c['review'] else '无规则报警')
            review=f"[{c['review']} 条]({ref(d/'待人工核验'/'请看这里.md',root)})" if c['review'] else '无'
            prior=LABEL[series[-2][1]['status']] if len(series)>1 else '首次登记'
            repairs=next((v['repair_note'] for _,v in reversed(series) if v.get('repair_note')),'无修复记录')
            rows.append(f"| {paper} | {pages if pages is not None else '未知'} | {state} | {repairs} | {issues} | {review} | {prior} | {calls:,} / {tokens:,} | {' · '.join(docs)} |")
        lines=['# LinguaFloat 翻译质量测试总表','',f"累计 **{len(hist)} 篇**，已知原文 **{totals['pages']} 页**"+(f"（另有 {totals['unknown_pages']} 篇页数未知）" if totals['unknown_pages'] else '')+'。'+ '；'.join(f'{k} {n} 篇' for k,n in states.items())+'。',f"已登记的真实翻译请求 **{totals['requests']:,} 次**，供应商已报告 **{totals['tokens']:,} Token**。离线复查不重新计费，未回报用量不估算。",'','每篇原文一行，按文件内容去重。点击论文名打开本次核查的双语 PDF；最新结论只代表表中最近一次检查，不等于逐句科学审校。保留记录不等于确定漏译。','',
               '| 论文 / 双语 PDF | 原文页数 | 最新结论 | 最近一次已验证的修复 | 仍存在的问题 | 待人工核验 | 上次结论 | 累计请求 / Token | 证据 |',
               '|---|---:|---|---|---|---|---|---:|---|',*rows]
        path=root/TITLE
        with NamedTemporaryFile('w',encoding='utf8',dir=root,delete=False) as tmp:
            tmp.write('\n'.join(lines)+'\n')
        try: os.replace(tmp.name,path)
        finally: Path(tmp.name).unlink(missing_ok=True)
    return path
