# -*- coding: utf-8 -*-
"""生成「清禾综合医院·医用耗材与批次追溯」AI 数智化后流程图 (自包含 HTML/SVG)。"""

W, H = 1300, 1500

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

parts = []
parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'font-family="-apple-system,Segoe UI,Microsoft YaHei,Helvetica,Arial,sans-serif" '
             f'style="max-width:100%;height:auto;background:#ffffff">')

parts.append('''
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 Z" fill="#334155"/></marker>
  <marker id="arrowRed" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 Z" fill="#dc2626"/></marker>
  <marker id="arrowGray" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 Z" fill="#64748b"/></marker>
  <marker id="arrowPurple" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 Z" fill="#7c3aed"/></marker>
  <marker id="arrowTeal" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 Z" fill="#0d9488"/></marker>
  <filter id="sh" x="-20%" y="-20%" width="140%" height="140%">
    <feDropShadow dx="0" dy="2" stdDeviation="2.2" flood-color="#0f172a" flood-opacity="0.14"/></filter>
</defs>
''')

def text_block(cx, cy, lines, fs=13, color="#0f172a", weight="400", anchor="middle", lh=None):
    lh = lh or (fs + 5)
    n = len(lines); start = cy - (n - 1) * lh / 2
    s = f'<text text-anchor="{anchor}" font-size="{fs}" font-weight="{weight}" fill="{color}">'
    for i, ln in enumerate(lines):
        s += f'<tspan x="{cx}" y="{start + i*lh}">{esc(ln)}</tspan>'
    s += '</text>'
    return s

def rect(x, y, w, h, fill, stroke, sw=2, rx=12):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" filter="url(#sh)"/>')

def process(x, y, w, h, lines, fill="#eef2ff", stroke="#4f46e5", fs=12.5):
    p = rect(x, y, w, h, fill, stroke)
    p += text_block(x + w/2, y + h/2, lines, fs=fs, weight="600")
    return p

def pill(x, y, w, h, lines, fill, stroke, fs=13):
    p = rect(x, y, w, h, fill, stroke, sw=2, rx=h/2)
    p += text_block(x + w/2, y + h/2, lines, fs=fs, weight="700", color="#ffffff")
    return p

def diamond(cx, cy, w, h, lines, fill="#fff7e6", stroke="#d97706", fs=12.5):
    x, y = cx - w/2, cy - h/2
    pts = f"{cx},{y} {x+w},{cy} {cx},{y+h} {x},{cy}"
    p = f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" filter="url(#sh)"/>'
    p += text_block(cx, cy, lines, fs=fs, weight="600")
    return p

def edge(x1, y1, x2, y2, kind="solid", color="#334155", marker="arrow", label=None):
    dash = ' stroke-dasharray="6,5"' if kind == "dashed" else ""
    p = (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2"{dash} '
         f'marker-end="url(#{marker})"/>')
    if label:
        lx, ly = (x1+x2)/2, (y1+y2)/2 - 6
        p += f'<rect x="{lx-22}" y="{ly-13}" width="44" height="18" rx="4" fill="#ffffff" stroke="{color}" stroke-width="1"/>'
        p += text_block(lx, ly, [label], fs=11, weight="700", color=color)
    return p

def path_returns(pts, color="#64748b", label=None):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    p = (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" stroke-dasharray="6,5" '
         f'marker-end="url(#arrowGray)"/>')
    if label:
        mx = sum(p_[0] for p_ in pts)/len(pts); my = sum(p_[1] for p_ in pts)/len(pts)
        p += f'<rect x="{mx-70}" y="{my-13}" width="140" height="18" rx="4" fill="#ffffff" stroke="{color}" stroke-width="1"/>'
        p += text_block(mx, my, [label], fs=10.5, weight="600", color=color)
    return p

# ===== 主流程脊柱 (AI 赋能) =====
SPW, SPX = 250, 300
BX0 = SPX - SPW/2
yS,yA,yD1,yB,yC,yD2,yE,yD3,yF,yG,yEND = (57,157,274,397,507,624,747,864,987,1097,1197)
BH, DH = 74, 100

parts.append(pill(BX0, yS-27, SPW, 54, ["开始 · 使用计划", "(AI 汇总排程+历史用量→智能请领建议)"], "#16a34a", "#15803d", fs=12.5))
parts.append(process(BX0, yA-BH/2, SPW, BH, ["① 库存统一视图", "AI 融合 现存量/已预留/在途 → 单一可信"]))
parts.append(diamond(SPX, yD1, 230, DH, ["AI 判库存可满足", "手术且未被预留?"]))
parts.append(process(BX0, yB-BH/2, SPW, BH, ["② 科室领用", "AI 自动生成领用单·草稿自动暂存同步"]))
parts.append(process(BX0, yC-BH/2, SPW, BH, ["③ 使用扫码", "AI 视觉/OCR 自动比对内外包装批次"]))
parts.append(diamond(SPX, yD2, 230, DH, ["AI 判批次与", "出库一致?"]))
parts.append(process(BX0, yE-BH/2, SPW, BH, ["④ 退库", "AI 主编号/版本自动匹配·回写可用"]))
parts.append(diamond(SPX, yD3, 230, DH, ["AI 判编码统一", "且已恢复可用?"]))
parts.append(process(BX0, yF-BH/2, SPW, BH, ["⑤ 补货提醒", "AI 预测性补货(人工确认不下发)"]))
parts.append(process(BX0, yG-BH/2, SPW, BH, ["⑥ 验收 (REQ-6)", "AI 自动统计 完整率/误发率"]))
parts.append(pill(BX0, yEND-27, SPW, 54, ["试点运行 / 每周复测改进"], "#0ea5e9", "#0369a1", fs=13))

for y1, y2 in [(yS+27, yA-BH/2),(yA+BH/2, yD1-DH/2),(yD1+DH/2, yB-BH/2),
               (yB+BH/2, yC-BH/2),(yC+BH/2, yD2-DH/2),(yD2+DH/2, yE-BH/2),
               (yE+BH/2, yD3-DH/2),(yD3+DH/2, yF-BH/2),(yF+BH/2, yG-BH/2),(yG+BH/2, yEND-27)]:
    parts.append(edge(SPX, y1, SPX, y2))

# ===== 右侧 AI 改造卡 =====
EXW, EXC = 350, 880
EX0 = EXC - EXW/2

def aibox(y, h, tag, title, lines):
    p = rect(EX0, y, EXW, h, "#f0fdfa", "#0d9488", sw=2, rx=12)
    p += f'<rect x="{EX0}" y="{y}" width="{EXW}" height="26" rx="12" fill="#0d9488"/>'
    p += f'<rect x="{EX0}" y="{y+13}" width="{EXW}" height="13" fill="#0d9488"/>'
    p += text_block(EX0+12, y+18, [tag], fs=12.5, weight="700", color="#fff", anchor="start")
    p += text_block(EXC, y+46, [title], fs=13, weight="700")
    p += text_block(EXC, y+70, lines, fs=11, color="#334155", lh=16)
    return p

parts.append(aibox(195, 130, "AI 改造 · REQ-1 [原P0]", "三口径分裂 → 自动融合",
    ["AI 三口径融合引擎生成单一可信库存视图",
     "实时预警缺口/预留冲突(替代人工对账)",
     "治理口径仍由 顾明岚 终审签字"]))
parts.append(aibox(405, 120, "AI 改造 · REQ-4 [原P1]", "草稿/交接 → 自动暂存",
    ["领用单/草稿 AI 自动生成并同步",
     "交接模板化, 含编号/版本/状态/接手人",
     "模板定稿由 唐映秋 确认"]))
parts.append(aibox(560, 135, "AI 改造 · REQ-2 [原P0]", "内外包装不一致 → 自动比对",
    ["AI 视觉/OCR 内外包装批次自动一致性校验",
     "常规自动通过, 仅异常转人工",
     "傅景 保留否决权·不覆盖原记录"]))
parts.append(aibox(800, 135, "AI 改造 · REQ-3 [原P0]", "编码/退库 → 自动匹配回写",
    ["AI 主编号/版本自动匹配, 退库自动回写",
     "歧义才进人工修复队列(秦芮)",
     "映射由 顾明岚+傅景 共同确认"]))
parts.append(aibox(1010, 115, "AI 改造 · REQ-5 [原P1]", "数据时效/接口 → 监控兜底",
    ["AI 数据时效监控, 超时标'待刷新'",
     "接口中断用带时间戳只读快照",
     "重连按业务编号去重补齐"]))

# No 分支
parts.append(edge(SPX+115, yD1, EX0, yD1, kind="dashed", color="#dc2626", marker="arrowRed", label="否"))
parts.append(edge(SPX+115, yD2, EX0, yD2, kind="dashed", color="#dc2626", marker="arrowRed", label="否"))
parts.append(edge(SPX+115, yD3, EX0, yD3, kind="dashed", color="#dc2626", marker="arrowRed", label="否"))
# 返回
parts.append(path_returns([(EX0,257),(120,257),(120,yA),(BX0,yA)], label="统一后重试 ①"))
parts.append(path_returns([(EX0,622),(120,622),(120,yC),(BX0,yC)], label="复核后重试 ③"))
parts.append(path_returns([(EX0,862),(120,862),(120,yE),(BX0,yE)], label="修复后重试 ④"))
# 贯穿 (紫) REQ-4/5
parts.append(edge(EX0,465, BX0+SPW, yB, kind="dashed", color="#7c3aed", marker="arrowPurple", label="贯穿"))
parts.append(edge(EX0,465, BX0+SPW, yC, kind="dashed", color="#7c3aed", marker="arrowPurple"))
parts.append(edge(EX0,1065, BX0+SPW, yG, kind="dashed", color="#7c3aed", marker="arrowPurple", label="贯穿"))

# 人工减负 callout (左)
parts.append(rect(30, 150, 168, 86, "#fef9c3", "#ca8a04", sw=2, rx=10))
parts.append(text_block(114, 172, ["人工减负", "100% 抽检"], fs=12, weight="700", color="#854d0e"))
parts.append(text_block(114, 192, ["↓"], fs=14, weight="700", color="#854d0e"))
parts.append(text_block(114, 212, ["仅 ~5% 异常", "转入人工(HITL)"], fs=11, color="#854d0e"))

# ===== 图例 =====
ly = 1290
parts.append(rect(40, ly, 560, 95, "#f8fafc", "#cbd5e1", sw=1, rx=10))
parts.append(text_block(60, ly+18, ["图例"], fs=12.5, weight="700", anchor="start"))
parts.append(rect(60, ly+30, 22, 14, "#eef2ff", "#4f46e5", sw=2, rx=4))
parts.append(text_block(92, ly+41, ["AI 赋能主流程 (执行点)"], fs=11.5, anchor="start"))
parts.append(rect(360, ly+30, 22, 14, "#fff7e6", "#d97706", sw=2, rx=4))
parts.append(text_block(392, ly+41, ["AI 决策/判断点"], fs=11.5, anchor="start"))
parts.append(rect(60, ly+54, 22, 14, "#f0fdfa", "#0d9488", sw=2, rx=4))
parts.append(text_block(92, ly+65, ["AI 自动化改造卡 (原 REQ)"], fs=11.5, anchor="start"))
parts.append(rect(60, ly+76, 22, 14, "#fef9c3", "#ca8a04", sw=2, rx=4))
parts.append(text_block(92, ly+87, ["人工减负 / HITL 兜底"], fs=11.5, anchor="start"))
parts.append(edge(330, ly+38, 370, ly+38, kind="dashed", color="#dc2626", marker="arrowRed"))
parts.append(text_block(378, ly+41, ["否→异常"], fs=10.5, anchor="start", color="#dc2626"))
parts.append(path_returns([(330, ly+68),(370, ly+68)], color="#64748b", label="人工处理后返回"))

# ===== 数据隐私合规护栏 (底部色带) =====
py = 1400
parts.append(rect(40, py, 1220, 78, "#0f172a", "#0f172a", sw=0, rx=12))
parts.append(text_block(60, py+20, ["数据隐私合规护栏（贯穿全程）"], fs=13, weight="700", anchor="start", color="#ffffff"))
parts.append(text_block(60, py+44, [
    "院内私有化部署 · 数据不出院 · 字段级脱敏 · 全程审计留痕 · 等保2.0三级 · 按角色最小授权",
    "合规依据：个人信息保护法 / 数据安全法 / 医疗卫生机构网络安全管理办法 · 模型本地推理, 不上传云端"],
    fs=11.5, anchor="start", color="#e2e8f0", lh=18))

parts.append('</svg>')

svg = "\n".join(parts)

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>清禾综合医院 · AI 数智化后流程图</title>
<style>
  body {{ margin:0; background:#f1f5f9; color:#0f172a; font-family:-apple-system,Segoe UI,Microsoft YaHei,Helvetica,Arial,sans-serif; }}
  header {{ background:#0f172a; color:#fff; padding:18px 28px; }}
  header h1 {{ margin:0; font-size:19px; font-weight:700; }}
  header p {{ margin:6px 0 0; font-size:13px; opacity:.8; }}
  .wrap {{ max-width:1200px; margin:22px auto; padding:0 16px; }}
  .cards {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:18px; }}
  .card {{ background:#fff; border-left:5px solid; border-radius:10px; padding:14px 16px; box-shadow:0 3px 12px rgba(15,23,42,.07); }}
  .card h3 {{ margin:0 0 6px; font-size:14.5px; }}
  .card p {{ margin:0; font-size:12.5px; color:#475569; line-height:1.7; }}
  .c1 {{ border-color:#4f46e5; }} .c2 {{ border-color:#0d9488; }} .c3 {{ border-color:#0f172a; }}
  .card svg {{ width:18px; height:18px; vertical-align:-3px; margin-right:4px; }}
  .box {{ background:#fff; border-radius:14px; box-shadow:0 4px 18px rgba(15,23,42,.08); padding:10px; }}
  .note {{ font-size:12.5px; color:#475569; margin:14px 4px 0; line-height:1.7; }}
  .note b {{ color:#0f172a; }}
</style>
</head>
<body>
<header>
  <h1>清禾综合医院 · 医用耗材与批次追溯 — AI 数智化后流程</h1>
  <p>原则：常规校验交给 AI，不可逆/治理决策仍留人工（HITL 精简而非取消）· 数据院内私有化</p>
</header>
<div class="wrap">
  <div class="cards">
    <div class="card c1">
      <h3>痛点① 费钱费时</h3>
      <p><b>AI 解法：</b>三口径自动融合生成单一可信库存视图；OCR/视觉自动比对批次；预测性补货。替代人工对账、盘点与录入，工时大幅下降。</p>
    </div>
    <div class="card c2">
      <h3>痛点② 人工审核</h3>
      <p><b>AI 解法：</b>AI 处理 100% 常规校验，仅约 5% 异常转入人工（HITL 精简）。傅景保留否决权、顾明岚保留口径签字权，红线不变。</p>
    </div>
    <div class="card c3">
      <h3>痛点③ 数据隐私</h3>
      <p><b>合规解法：</b>院内私有化部署、数据不出院；字段级脱敏；全程审计留痕；等保2.0三级；模型本地推理不上云。满足个保法/数据安全法。</p>
    </div>
  </div>
  <div class="box">
    {svg}
  </div>
  <p class="note">
    <b>读法：</b>主线（蓝紫）全部由 AI 赋能——①库存统一视图→②科室领用→③使用扫码→④退库→⑤补货→⑥验收。
    橙色菱形为 AI 判断点，条件不满足（<b>否</b>，红虚线）转入右侧对应改造卡，仅异常由具名人工（傅景/顾明岚/秦芮）兜底后（<b>灰虚线</b>）返回重试。
    左侧黄色卡量化"人工减负"：审核量由 100% 抽检降至约 5% 异常。底部黑色色带为<b>数据隐私合规护栏</b>，贯穿全程。
    所有原报告红线（不自动改临床选择、工程师不得单方设口径、唐映秋不能代签核）在 AI 方案中均保留。
  </p>
</div>
</body>
</html>'''

out = "C:/Users/lenovo/WorkBuddy/2026-10-23-10-08-46/flowchart_ai.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out, "bytes:", len(html))
