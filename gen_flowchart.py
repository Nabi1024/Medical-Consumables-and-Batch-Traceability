# -*- coding: utf-8 -*-
"""生成「清禾综合医院 · 医用耗材与批次追溯」项目流程图 (自包含 HTML/SVG)。"""

# ---------- 基础参数 ----------
W, H = 1280, 1400
SVG_NS = ""

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

parts = []
parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'font-family="-apple-system,Segoe UI,Microsoft YaHei,Helvetica,Arial,sans-serif" '
             f'style="max-width:100%;height:auto;background:#ffffff">')

# 定义标记与滤镜
parts.append('''
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 Z" fill="#334155"/>
  </marker>
  <marker id="arrowRed" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 Z" fill="#dc2626"/>
  </marker>
  <marker id="arrowGray" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 Z" fill="#64748b"/>
  </marker>
  <marker id="arrowPurple" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 Z" fill="#7c3aed"/>
  </marker>
  <filter id="sh" x="-20%" y="-20%" width="140%" height="140%">
    <feDropShadow dx="0" dy="2" stdDeviation="2.2" flood-color="#0f172a" flood-opacity="0.14"/>
  </filter>
</defs>
''')

# ---------- 绘制工具 ----------
def text_block(cx, cy, lines, fs=13, color="#0f172a", weight="400", anchor="middle", lh=None):
    lh = lh or (fs + 5)
    n = len(lines)
    start = cy - (n - 1) * lh / 2
    s = f'<text text-anchor="{anchor}" font-size="{fs}" font-weight="{weight}" fill="{color}">'
    for i, ln in enumerate(lines):
        y = start + i * lh
        s += f'<tspan x="{cx}" y="{y}">{esc(ln)}</tspan>'
    s += '</text>'
    return s

def rect(x, y, w, h, fill, stroke, sw=2, rx=12):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" filter="url(#sh)"/>')

def process(x, y, w, h, lines, fill="#ffffff", stroke="#1d4ed8", fs=13):
    p = rect(x, y, w, h, fill, stroke)
    p += text_block(x + w / 2, y + h / 2, lines, fs=fs, weight="600")
    return p

def pill(x, y, w, h, lines, fill, stroke, fs=14):
    p = rect(x, y, w, h, fill, stroke, sw=2, rx=h/2)
    p += text_block(x + w / 2, y + h / 2, lines, fs=fs, weight="700", color="#ffffff")
    return p

def diamond(cx, cy, w, h, lines, fill="#fff7e6", stroke="#d97706", fs=12.5):
    x, y = cx - w/2, cy - h/2
    pts = f"{cx},{y} {x+w},{cy} {cx},{y+h} {x},{cy}"
    p = f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" filter="url(#sh)"/>'
    p += text_block(cx, cy, lines, fs=fs, weight="600")
    return p

def edge(x1, y1, x2, y2, kind="solid", color="#334155", marker="arrow", label=None, lx=None, ly=None):
    dash = ' stroke-dasharray="6,5"' if kind == "dashed" else ""
    p = (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
         f'stroke-width="2"{dash} marker-end="url(#{marker})"/>')
    if label:
        lx = lx if lx is not None else (x1 + x2) / 2
        ly = ly if ly is not None else (y1 + y2) / 2 - 6
        p += (f'<rect x="{lx-22}" y="{ly-13}" width="44" height="18" rx="4" fill="#ffffff" '
              f'stroke="{color}" stroke-width="1"/>')
        p += text_block(lx, ly, [label], fs=11, weight="700", color=color)
    return p

def path_returns(pts, color="#64748b", label=None, lpos=0.55):
    # pts: list of (x,y); draw polyline with rounded-ish segments
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    p = (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" '
         f'stroke-dasharray="6,5" marker-end="url(#arrowGray)"/>')
    if label:
        # place near middle segment
        mx = sum(p_[0] for p_ in pts) / len(pts)
        my = sum(p_[1] for p_ in pts) / len(pts)
        p += (f'<rect x="{mx-66}" y="{my-13}" width="132" height="18" rx="4" fill="#ffffff" '
              f'stroke="{color}" stroke-width="1"/>')
        p += text_block(mx, my, [label], fs=10.5, weight="600", color=color)
    return p

# ================= 主流程脊柱 =================
SPX, SPW = 300, 250   # 主框中心x, 宽
BX0 = SPX - SPW/2     # 180

# 节点 y 中心
yS, yA, yD1, yB, yC, yD2, yE, yD3, yF, yG, yEND = (
    57, 157, 274, 397, 507, 624, 747, 864, 987, 1097, 1197)
BH = 74
DH = 100

parts.append(pill(BX0, yS-27, SPW, 54, ["开始 · 使用计划", "(科室提交手术耗材计划)"], "#16a34a", "#15803d", fs=13))
parts.append(process(BX0, yA-BH/2, SPW, BH, ["① 库存核查", "现存量 / 已预留 / 在途 三口径"], fill="#eff6ff", stroke="#1d4ed8"))
parts.append(diamond(SPX, yD1, 230, DH, ["三口径一致且", "库存可满足手术?"]))
parts.append(process(BX0, yB-BH/2, SPW, BH, ["② 科室领用", "(领用≠实际使用同日)"]))
parts.append(process(BX0, yC-BH/2, SPW, BH, ["③ 使用扫码", "植入耗材批次核对"]))
parts.append(diamond(SPX, yD2, 230, DH, ["内外包装批次", "与出库一致?"]))
parts.append(process(BX0, yE-BH/2, SPW, BH, ["④ 退库", "主编号/事件时间/版本硬门槛"]))
parts.append(diamond(SPX, yD3, 230, DH, ["编码统一且", "退库已恢复可用?"]))
parts.append(process(BX0, yF-BH/2, SPW, BH, ["⑤ 补货提醒", "不自动改变临床选择"]))
parts.append(process(BX0, yG-BH/2, SPW, BH, ["⑥ 验收 (REQ-6)", "单科室·完整率100%·误发0"]))
parts.append(pill(BX0, yEND-27, SPW, 54, ["试点运行 / 每周复测改进"], "#0ea5e9", "#0369a1", fs=13))

# 主竖向连线 (Yes)
main_edges = [
    (yS+27, yA-BH/2), (yA+BH/2, yD1-DH/2), (yD1+DH/2, yB-BH/2),
    (yB+BH/2, yC-BH/2), (yC+BH/2, yD2-DH/2), (yD2+DH/2, yE-BH/2),
    (yE+BH/2, yD3-DH/2), (yD3+DH/2, yF-BH/2), (yF+BH/2, yG-BH/2),
    (yG+BH/2, yEND-27),
]
for y1, y2 in main_edges:
    parts.append(edge(SPX, y1, SPX, y2))

# ================= 右侧异常/需求框 =================
EXW, EXC = 360, 882
EX0 = EXC - EXW/2  # 702

def exbox(y, h, tag, title, lines, p0=True):
    stroke = "#dc2626" if p0 else "#2563eb"
    fill = "#fef2f2" if p0 else "#eff6ff"
    tagfill = "#dc2626" if p0 else "#2563eb"
    p = rect(EX0, y, EXW, h, fill, stroke, sw=2, rx=12)
    # 标签条
    p += f'<rect x="{EX0}" y="{y}" width="{EXW}" height="26" rx="12" fill="{tagfill}"/>'
    p += f'<rect x="{EX0}" y="{y+13}" width="{EXW}" height="13" fill="{tagfill}"/>'
    p += text_block(EX0+12, y+18, [tag], fs=12.5, weight="700", color="#ffffff", anchor="start")
    # 标题
    p += text_block(EXC, y+46, [title], fs=13.5, weight="700", color="#0f172a")
    # 内容
    p += text_block(EXC, y+70, lines, fs=11.5, color="#334155", lh=17)
    return p

parts.append(exbox(200, 132, "REQ-1 [P0]", "库存口径分裂 → 术前缺货",
    ["现存量(库房)/已预留(科室)/在途(供应商)三方各算",
     "缺货率 8%→目标 2% 直接相关",
     "→ 先统一口径定义，不得工程师单方设定",
     "人工确认: 顾明岚 签字"]))

parts.append(exbox(540, 142, "REQ-2 [P0]", "批次内外包装不一致 → 不可逆风险",
    ["外包装扫码成功，内包装批次≠出库批次",
     "→ 强制转人工，标注触发原因/接手人/确认时间",
     "傅景定规则 · 唐映秋分派 · 错误不覆盖原记录",
     "停止条件: 傅景 可否决上线"]))

parts.append(exbox(790, 142, "REQ-3 [P0]", "编码不统一 / 退库未恢复可用",
    ["供应商码≠院内码，退库未回写可用状态",
     "主编号/事件时间/版本 为硬门槛",
     "歧义记录进人工修复队列(不进自动推荐)",
     "秦芮出映射 · 顾明岚+傅景 共同确认"]))

parts.append(exbox(410, 120, "REQ-4 [P1]", "草稿暂存与交接确认 (贯穿)",
    ["便签/群消息补录未随单据交接",
     "→ 草稿可暂存+最后同步时间",
     "交接含 编号/版本/状态/未解决/接手人",
     "模板定稿: 唐映秋 确认"]))

parts.append(exbox(1010, 120, "REQ-5 [P1]", "数据时效与接口兜底 (贯穿)",
    ["每小时更新，超时标「待刷新」不作实时承诺",
     "接口中断用带时间戳只读快照",
     "重连按业务编号去重补齐，人工流程继续",
     "阻断/提示选择: 顾明岚+傅景 确认"]))

# No 分支 (红虚线 决策右→异常左)
parts.append(edge(SPX+115, yD1, EX0, yD1, kind="dashed", color="#dc2626", marker="arrowRed", label="否"))
parts.append(edge(SPX+115, yD2, EX0, yD2, kind="dashed", color="#dc2626", marker="arrowRed", label="否"))
parts.append(edge(SPX+115, yD3, EX0, yD3, kind="dashed", color="#dc2626", marker="arrowRed", label="否"))

# 返回路径 (灰虚线 异常左→对应步骤左)
parts.append(path_returns([(EX0, 262), (120, 262), (120, yA), (BX0, yA)], label="统一口径后重试 ①"))
parts.append(path_returns([(EX0, 602), (120, 602), (120, yC), (BX0, yC)], label="转人工复核后重试 ③"))
parts.append(path_returns([(EX0, 852), (120, 852), (120, yE), (BX0, yE)], label="人工修复后重试 ④"))

# 贯穿虚线 (紫)
parts.append(edge(EX0, 470, BX0+SPW, yB, kind="dashed", color="#7c3aed", marker="arrowPurple", label="贯穿"))
parts.append(edge(EX0, 470, BX0+SPW, yC, kind="dashed", color="#7c3aed", marker="arrowPurple"))
parts.append(edge(EX0, 1070, BX0+SPW, yG, kind="dashed", color="#7c3aed", marker="arrowPurple", label="贯穿"))

# ================= 图例 =================
ly = 1290
parts.append(rect(40, ly, 560, 95, "#f8fafc", "#cbd5e1", sw=1, rx=10))
parts.append(text_block(60, ly+18, ["图例"], fs=12.5, weight="700", anchor="start"))
parts.append(rect(60, ly+30, 22, 14, "#ffffff", "#1d4ed8", sw=2, rx=4))
parts.append(text_block(92, ly+41, ["主流程步骤 (执行点)"], fs=11.5, anchor="start"))
parts.append(rect(320, ly+30, 22, 14, "#fff7e6", "#d97706", sw=2, rx=4))
parts.append(text_block(352, ly+41, ["决策/判断点"], fs=11.5, anchor="start"))
parts.append(rect(60, ly+54, 22, 14, "#fef2f2", "#dc2626", sw=2, rx=4))
parts.append(text_block(92, ly+65, ["P0 高风险 / 不可逆 (须人工兜底)"], fs=11.5, anchor="start"))
parts.append(rect(60, ly+76, 22, 14, "#eff6ff", "#2563eb", sw=2, rx=4))
parts.append(text_block(92, ly+87, ["P1 优化项 / 贯穿机制"], fs=11.5, anchor="start"))
# 红虚线 & 灰虚线说明
parts.append(edge(300, ly+38, 340, ly+38, kind="dashed", color="#dc2626", marker="arrowRed"))
parts.append(text_block(348, ly+41, ["否→异常"], fs=10.5, anchor="start", color="#dc2626"))
parts.append(path_returns([(300, ly+68), (340, ly+68)], color="#64748b", label="人工处理后返回"))

# 角色栏
parts.append(rect(620, ly, 620, 95, "#f8fafc", "#cbd5e1", sw=1, rx=10))
parts.append(text_block(640, ly+18, ["角色与职责边界"], fs=12.5, weight="700", anchor="start"))
roles = [
    ("顾明岚", "耗材管理主任 · 立项/口径签字/验收口径"),
    ("林亦舟", "手术室护士 · 一线操作/异常暂停"),
    ("唐映秋", "采购管理员 · 分派/交接模板/不能代签核"),
    ("秦芮", "物资工程师 · 数据口径/接口/映射草案"),
    ("傅景", "院感质控专员 · 红线否决/留痕规则"),
    ("叶书", "临床科室主任 · 服务侧验收/反馈闭环"),
]
ry = ly + 40
for i, (name, desc) in enumerate(roles):
    col = 640 + (i % 2) * 300
    row = ry + (i // 2) * 22
    parts.append(text_block(col, row, [f"● {name}：{desc}"], fs=10.5, anchor="start", color="#0f172a"))

parts.append('</svg>')

svg = "\n".join(parts)

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>清禾综合医院 · 医用耗材与批次追溯 项目流程图</title>
<style>
  body {{ margin:0; background:#f1f5f9; color:#0f172a; font-family:-apple-system,Segoe UI,Microsoft YaHei,Helvetica,Arial,sans-serif; }}
  header {{ background:#0f172a; color:#fff; padding:18px 28px; }}
  header h1 {{ margin:0; font-size:19px; font-weight:700; }}
  header p {{ margin:6px 0 0; font-size:13px; opacity:.8; }}
  .wrap {{ max-width:1180px; margin:22px auto; padding:0 16px; }}
  .card {{ background:#fff; border-radius:14px; box-shadow:0 4px 18px rgba(15,23,42,.08); padding:10px; }}
  .note {{ font-size:12.5px; color:#475569; margin:14px 4px 0; line-height:1.7; }}
  .note b {{ color:#0f172a; }}
</style>
</head>
<body>
<header>
  <h1>清禾综合医院 · 医用耗材与批次追溯</h1>
  <p>业务需求发现报告 → 端到端流程图（含异常分支 / 人工兜底 / 角色职责）· 需求均为待验证假设</p>
</header>
<div class="wrap">
  <div class="card">
    {svg}
  </div>
  <p class="note">
    <b>读法：</b>左侧绿色为起点，沿蓝色主线依次经过 ①库存核查 → ②科室领用 → ③使用扫码 → ④退库 → ⑤补货 → ⑥验收。
    橙色菱形为判断点：条件不满足（<b>否</b>，红虚线）即转入右侧对应 REQ 异常处理；人工确认/修复后（<b>灰虚线</b>）返回原步骤重试。
    REQ-4 / REQ-5 为贯穿全程机制（紫虚线）。所有 P0 高风险环节均设有具名人工接收人与否决权，体现「不可逆动作前确定性校验」原则。
  </p>
</div>
</body>
</html>'''

out = "C:/Users/lenovo/WorkBuddy/2026-10-23-10-08-46/flowchart.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out, "bytes:", len(html))
