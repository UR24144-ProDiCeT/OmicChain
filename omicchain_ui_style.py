"""Look of the application: style sheet, header, state of each step. Display only."""

import html

import streamlit as st

ACCENT, ACCENT_DARK, ACCENT_SOFT = "#0f766e", "#0a4f49", "#e3f1ef"
INK, MUTED, LINE, GROUND = "#17242b", "#55666e", "#dde3e6", "#f4f6f7"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, .stApp, .stApp p, .stApp label, .stApp li, .stApp input, .stApp textarea, .stApp button {{
    font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
}}
.stApp {{ background: {GROUND}; color: {INK}; }}
.block-container {{ padding-top: 1.2rem !important; max-width: 1320px; }}
[data-testid="stHeader"] {{ background: transparent; }}
h1, h2, h3, h4 {{ font-family: 'Space Grotesk', 'Inter', sans-serif !important; font-weight: 600 !important;
                 letter-spacing: -0.01em; color: {INK}; }}
h2 {{ font-size: 1.55rem !important; }}
h3 {{ font-size: 1.2rem !important; }}
hr {{ border-color: {LINE} !important; margin: 1.1rem 0 !important; }}

.om-head {{ display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 14px;
           background: #ffffff; border: 1px solid {LINE}; border-radius: 14px; padding: 14px 20px; margin-bottom: 14px; }}
.om-brand {{ display: flex; align-items: center; gap: 12px; }}
.om-logo {{ display: block; width: auto; object-fit: cover; border-radius: 9px; }}
.om-name {{ font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 21px; line-height: 1.1; color: {INK}; }}
.om-tag {{ font-size: 12.5px; color: {MUTED}; }}
.om-chips {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
.om-chip {{ display: inline-flex; align-items: center; gap: 7px; padding: 6px 12px; border: 1px solid {LINE};
           border-radius: 999px; font-size: 12.5px; color: #33454d; background: #ffffff; }}
.om-chip.mono {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; }}
.om-chip.ok {{ background: {ACCENT_SOFT}; border-color: {ACCENT_SOFT}; color: {ACCENT_DARK}; font-weight: 500; }}
.om-chip.test {{ background: #fdf3d7; border-color: #f3dc9a; color: #6b4e00; }}
.om-dot {{ width: 8px; height: 8px; border-radius: 50%; background: {ACCENT}; display: inline-block; }}
.om-label {{ font-size: 12px; font-weight: 600; color: {MUTED}; text-transform: uppercase; letter-spacing: 0.05em;
            margin-bottom: 2px; }}

.stTabs [role="tablist"] {{ gap: 10px !important; flex-wrap: wrap; counter-reset: omstep; border: 0 !important; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"],
.stTabs .react-aria-SelectionIndicator {{ display: none !important; }}
.stTabs [role="tablist"], .stTabs > div {{ border-bottom: 0 !important; box-shadow: none !important; }}
.stTabs [role="tab"] {{
    counter-increment: omstep; flex: 1 1 150px; height: auto !important; min-height: 62px;
    display: grid !important; grid-template-columns: 26px 1fr; column-gap: 10px; align-items: center; justify-items: start;
    text-align: left; padding: 10px 14px !important; background: #ffffff; border: 1px solid {LINE} !important;
    border-radius: 12px !important; color: {INK} !important; }}
.stTabs [role="tab"]::before {{
    content: counter(omstep); grid-row: 1 / span 2; width: 26px; height: 26px; border-radius: 50%; box-sizing: border-box;
    border: 1.5px solid #9aa9b0; color: {MUTED}; display: inline-flex; align-items: center; justify-content: center;
    font-weight: 600; font-size: 13px; }}
.stTabs [role="tab"]::after {{ grid-column: 2; font-size: 12px; color: {MUTED}; font-weight: 400;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }}
.stTabs [role="tab"] p {{ font-weight: 600 !important; font-size: 0.95rem !important; margin: 0; }}
.stTabs [role="tab"][aria-selected="true"] {{ background: {ACCENT}; border-color: {ACCENT} !important;
    color: #ffffff !important; }}
.stTabs [role="tab"][aria-selected="true"] p {{ color: #ffffff !important; }}
.stTabs [role="tab"][aria-selected="true"]::before {{ background: #ffffff; border-color: #ffffff; color: {ACCENT}; }}
.stTabs [role="tab"][aria-selected="true"]::after {{ color: #d4ece9; }}
.stTabs [role="tab"]:nth-child(5) {{ border-style: dashed !important; }}
.stTabs [role="tab"]:nth-child(5)::before {{ content: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 16 16'%3E%3Ccircle cx='7' cy='7' r='4.5' stroke='%2355666e' stroke-width='1.8' fill='none'/%3E%3Cpath d='M10.5 10.5L14 14' stroke='%2355666e' stroke-width='1.8' stroke-linecap='round'/%3E%3C/svg%3E");
    border: 0; background: #eef1f3; padding-top: 3px; }}
.stTabs [role="tab"]:nth-child(5)[aria-selected="true"]::before {{ background: #ffffff; }}
.stTabs [role="tabpanel"] {{ background: #ffffff; border: 1px solid {LINE}; border-radius: 14px;
    padding: 22px 26px 26px; margin-top: 14px; }}
.stTabs .stTabs [role="tabpanel"] {{ border: 0; padding: 12px 0 0; margin-top: 0; }}
.stTabs .stTabs [role="tablist"] {{ gap: 6px !important; }}
.stTabs .stTabs [role="tab"] {{ flex: 0 0 auto; min-height: 40px; display: inline-flex !important;
    padding: 6px 14px !important; border-radius: 8px !important; border-style: solid !important;
    background: #ffffff; color: {INK} !important; }}
.stTabs .stTabs [role="tab"]::before, .stTabs .stTabs [role="tab"]::after {{ display: none !important; }}
.stTabs .stTabs [role="tab"][aria-selected="true"] {{ background: {ACCENT_SOFT}; border-color: {ACCENT_SOFT} !important; }}
.stTabs .stTabs [role="tab"][aria-selected="true"] p {{ color: {ACCENT_DARK} !important; }}

[data-testid="stVerticalBlockBorderWrapper"], [data-testid="stExpander"] details, .om-card,
[data-testid="stMetric"] {{
    background: #ffffff; border: 1px solid {LINE} !important; border-radius: 14px !important; }}
[data-testid="stMetric"] {{ padding: 14px 18px; }}
[data-testid="stMetricValue"] {{ font-family: 'Space Grotesk', sans-serif; font-weight: 600; }}
[data-testid="stExpander"] summary {{ font-weight: 500; }}
[data-testid="stAlert"] {{ border-radius: 12px; }}
[data-testid="stDataFrame"] {{ border-radius: 10px; overflow: hidden; }}

.stButton > button, .stDownloadButton > button, .stLinkButton > a {{
    min-height: 44px; border-radius: 10px !important; font-weight: 600; border: 1px solid #b8c4c9;
    background: #ffffff; color: {INK}; box-shadow: none; }}
.stButton > button:hover, .stDownloadButton > button:hover {{ border-color: {ACCENT}; color: {ACCENT_DARK}; }}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{
    background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; padding: 0 22px; }}
.stButton > button[kind="primary"]:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; color: #ffffff; }}
.stButton > button:disabled {{ opacity: 0.55; }}

[data-testid="stCode"] pre, .stCode pre {{ background: {INK} !important; border-radius: 12px !important; }}
[data-testid="stCode"] code, [data-testid="stCode"] code span, .stCode code, .stCode code span {{
    color: #e6ecee !important; font-family: 'JetBrains Mono', ui-monospace, Consolas, monospace !important; font-size: 0.83rem; }}
.stMarkdown code {{ font-family: 'JetBrains Mono', ui-monospace, Consolas, monospace; font-size: 0.85em;
    background: #e9edef; color: {INK}; border-radius: 5px; padding: 1px 5px; }}

[data-testid="stSidebar"] {{ background: #ffffff; border-right: 1px solid {LINE}; }}
[data-testid="stSidebar"] h2 {{ font-size: 1.1rem !important; }}
[data-testid="stSidebar"] [data-testid="stCode"] pre {{ background: #eef1f3 !important; padding: 8px 10px !important; }}
[data-testid="stSidebar"] [data-testid="stCode"] code, [data-testid="stSidebar"] [data-testid="stCode"] code span {{
    color: {INK} !important; font-size: 0.74rem; }}
</style>
"""

LOGO = ('<svg width="36" height="36" viewBox="0 0 34 34" aria-hidden="true"><rect width="34" height="34" rx="9" '
        f'fill="{ACCENT}"></rect><path d="M11 9c0 6 12 6 12 12M23 9c0 6-12 6-12 12M12.5 12.5h9M12.5 21.5h9" '
        'stroke="#ffffff" stroke-width="2" fill="none" stroke-linecap="round"></path></svg>')

def apply():
    st.markdown(CSS, unsafe_allow_html=True)

def short_address(address):
    address = str(address or "")
    return f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address

@st.cache_data(show_spinner=False)
def _image_tag(path_str, mtime, height=52, max_width=260):
    """<img> tag (reduced image embedded in the page) for the header; None if the file cannot be read."""
    import base64
    import io
    try:
        from PIL import Image
        img = Image.open(path_str)
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
        scale = (2 * height) / img.height
        if scale < 1:
            img = img.resize((max(1, round(img.width * scale)), 2 * height), Image.LANCZOS)
        buf = io.BytesIO()
        fmt = "PNG" if img.mode == "RGBA" else "JPEG"
        img.save(buf, format=fmt, **({} if fmt == "PNG" else {"quality": 88}))
    except Exception:
        return None
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return (f'<img src="data:image/{fmt.lower()};base64,{data}" alt="OmicChain" class="om-logo" '
            f'style="height: {height}px; max-width: {max_width}px">')

def brand_image(folder, names=("omicchain_logo.png", "omicchain_logo.jpg", "omicchain_banner.png", "omicchain_banner.jpg")):
    """Header image: a logo if the project folder has one, otherwise the banner."""
    from pathlib import Path
    for name in names:
        p = Path(folder) / name
        if p.is_file():
            return p
    return None

def header(network, account, orcid=None, image=None):
    """Header: logo, name, network, account, ORCID iD."""
    logo = None
    if image is not None:
        try:
            logo = _image_tag(str(image), image.stat().st_mtime)
        except OSError:
            logo = None
    logo = logo or LOGO
    testnet = "test" in network.lower()
    chips = [f'<span class="om-chip{" test" if testnet else ""}"><span class="om-dot"></span>{html.escape(network)}</span>']
    if account:
        chips.append(f'<span class="om-chip mono" title="{html.escape(str(account))}">{html.escape(short_address(account))}</span>')
    if orcid:
        chips.append(f'<span class="om-chip ok">ORCID {html.escape(orcid)} linked</span>')
    st.markdown(
        f'<div class="om-head"><div class="om-brand">{logo}<div><div class="om-name">OmicChain</div>'
        '<div class="om-tag">Certified RNA-seq analyses — analytical integrity on the blockchain</div></div></div>'
        f'<div class="om-chips">{"".join(chips)}</div></div>', unsafe_allow_html=True)

def label(text):
    """Small upper-case label above a value."""
    st.markdown(f'<div class="om-label">{html.escape(text)}</div>', unsafe_allow_html=True)

def _css_string(text):
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

def step_status(subtitles):
    """State shown under the name of each step, in the order of the main tabs."""
    rules = "".join(
        f'.stTabs [role="tab"]:nth-child({i})::after {{ content: "{_css_string(text)}"; }}\n'
        for i, text in enumerate(subtitles, start=1) if text)
    st.markdown(f"<style>\n{rules}</style>", unsafe_allow_html=True)
