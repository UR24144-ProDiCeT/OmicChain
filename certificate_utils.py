"""PDF certificate of analytical integrity (ReportLab), with an annex listing the certified raw data."""

from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as RLImage,
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.barcode.qr import QrCodeWidget


def _short_hash(value: str, chars: int = 12) -> str:
    if not value:
        return "N/A"
    return value if len(value) <= chars * 2 else f"{value[:chars]}…{value[-chars:]}"


def _safe(value, default="N/A"):
    return value if value not in (None, "") else default


def _make_qr(data: str, size: float = 28 * mm) -> Drawing:
    """Generate a QR code drawing."""
    qr = QrCodeWidget(data)
    bounds = qr.getBounds()

    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]

    drawing = Drawing(
        size,
        size,
        transform=[
            size / width,
            0,
            0,
            size / height,
            0,
            0,
        ],
    )

    drawing.add(qr)
    return drawing


def generate_certificate_pdf(
    output_path,
    bundle_hash,
    blockies_path=None,
    network=None,
    explorer=None,
    tx_hash=None,
    block_number=None,
    blockchain_timestamp=None,
    proof_contract=None,
    publisher_address=None,
    component_hashes=None,
    analysis_parameters=None,
    components=None,
    verify_url=None,
    publisher_identity=None,
    data_origin=None,
):
    """Generate the analytical integrity certificate as PDF: one page, plus an annex
    listing the certified raw data when the analysis has a data origin."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    component_hashes = component_hashes or {}
    analysis_parameters = analysis_parameters or {}


    if blockchain_timestamp:
        if isinstance(blockchain_timestamp, (int, float)):
            dt = datetime.fromtimestamp(
                blockchain_timestamp,
                tz=timezone.utc,
            )
        else:
            dt = blockchain_timestamp

        timestamp = dt.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    else:
        timestamp = "N/A"


    if tx_hash not in (None, ""):
        tx_hash = str(tx_hash).strip()
        if not tx_hash.lower().startswith("0x"):
            tx_hash = f"0x{tx_hash}"
    tx_hash = _safe(tx_hash)
    explorer = _safe(explorer)
    tx_for_url = tx_hash
    tx_url = (
        f"{explorer.rstrip('/')}/tx/{tx_for_url}"
        if explorer != "N/A" and tx_hash != "N/A"
        else None
    )

    qr_data = verify_url or tx_url or (bundle_hash or "")
    qr_caption = "Verify online" if verify_url else "Verify transaction"


    styles = getSampleStyleSheet()

    title = ParagraphStyle(
        "CertificateTitle",
        parent=styles["Title"],
        fontSize=20,
        leading=24,
        alignment=TA_CENTER,
        spaceAfter=3 * mm,
    )

    subtitle = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#555555"),
    )

    section = ParagraphStyle(
        "Section",
        parent=styles["Heading3"],
        fontSize=10,
        leading=12,
        textColor=colors.HexColor("#234a7c"),
        spaceBefore=3 * mm,
        spaceAfter=1.5 * mm,
    )

    body = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=8.2,
        leading=10.5,
    )

    small = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontSize=6.8,
        leading=8.5,
        textColor=colors.HexColor("#555555"),
        alignment=TA_CENTER,
    )

    hash_style = ParagraphStyle(
        "Hash",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=7.3,
        leading=9,
        wordWrap="CJK",
    )


    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=13 * mm,
        bottomMargin=12 * mm,
        title="OmicChain Analytical Integrity Certificate",
        author="OmicChain",
    )

    story = []


    story.append(
        Paragraph(
            "OMICCHAIN",
            title,
        )
    )

    story.append(
        Paragraph(
            "ANALYTICAL INTEGRITY CERTIFICATE",
            subtitle,
        )
    )

    story.append(
        Spacer(
            1,
            3 * mm,
        )
    )


    if blockies_path and Path(blockies_path).exists():

        icon = RLImage(
            str(blockies_path),
            width=38 * mm,
            height=38 * mm,
        )

        icon.hAlign = "CENTER"

        story.append(icon)

        story.append(
            Spacer(
                1,
                2 * mm,
            )
        )


    story.append(
        Paragraph(
            "Composite analytical fingerprint",
            section,
        )
    )

    story.append(
        Paragraph(
            _safe(bundle_hash),
            hash_style,
        )
    )


    story.append(
        Paragraph(
            "Fingerprint components",
            section,
        )
    )

    component_rows = [
        ["Component", "SHA-256"],
    ]

    labels = [
        ("Snakefile", "snakefile"),
        ("R analysis script", "rscript"),
        ("Analysis parameters", "parameters"),
        ("Input data (expression + metadata)", "data"),
        ("R environment (renv.lock)", "renv_lock"),
        ("Top DE genes table", "top_genes"),
    ]

    if components:
        rows_source = [(c["label"], c["sha256"]) for c in components]
    else:
        rows_source = [(label, component_hashes.get(key)) for label, key in labels]

    for label, digest in rows_source:

        component_rows.append(
            [
                Paragraph(
                    label,
                    body,
                ),
                Paragraph(
                    _safe(digest),
                    hash_style,
                ),
            ]
        )

    table = Table(
        component_rows,
        colWidths=[
            48 * mm,
            126 * mm,
        ],
        repeatRows=1,
    )

    table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#eef3f8"),
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#234a7c"),
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "Helvetica-Bold",
                ),
                (
                    "FONTSIZE",
                    (0, 0),
                    (-1, -1),
                    7.5,
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.35,
                    colors.HexColor("#c8d0d8"),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
            ]
        )
    )

    story.append(table)


    if analysis_parameters:
        story.append(
            Paragraph(
                "Analysis parameters",
                section,
            )
        )

        param_rows = [
            [
                "Adjusted p-value threshold",
                _safe(analysis_parameters.get("padj_threshold")),
            ],
            [
                "|log2 fold-change| threshold",
                _safe(analysis_parameters.get("lfc_threshold")),
            ],
            [
                "Control samples",
                Paragraph(
                    _safe(analysis_parameters.get("control_samples")),
                    body,
                ),
            ],
            [
                "Treatment samples",
                Paragraph(
                    _safe(analysis_parameters.get("treatment_samples")),
                    body,
                ),
            ],
        ]
        if analysis_parameters.get("covariate"):
            param_rows.append([
                "Model (DESeq2 design)",
                _safe(f"~ {analysis_parameters['covariate']} + group"),
            ])

        param_table = Table(
            param_rows,
            colWidths=[
                48 * mm,
                126 * mm,
            ],
        )

        param_table.setStyle(
            TableStyle(
                [
                    (
                        "GRID",
                        (0, 0),
                        (-1, -1),
                        0.35,
                        colors.HexColor("#c8d0d8"),
                    ),
                    (
                        "BACKGROUND",
                        (0, 0),
                        (0, -1),
                        colors.HexColor("#f6f8fa"),
                    ),
                    (
                        "FONTNAME",
                        (0, 0),
                        (0, -1),
                        "Helvetica-Bold",
                    ),
                    (
                        "FONTSIZE",
                        (0, 0),
                        (-1, -1),
                        7.5,
                    ),
                    (
                        "VALIGN",
                        (0, 0),
                        (-1, -1),
                        "TOP",
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        3,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        3,
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        3,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        3,
                    ),
                ]
            )
        )

        story.append(param_table)


    origin_rows = data_origin_rows(data_origin)
    if origin_rows:
        story.append(Paragraph("Data origin", section))
        origin_table = Table([[k, Paragraph(_safe(v), body)] for k, v in origin_rows],
                             colWidths=[48 * mm, 126 * mm])
        origin_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c8d0d8")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(origin_table)
        story.append(Spacer(1, 1 * mm))
        story.append(Paragraph("Every raw file is listed with its checksum in the annex (page 2).", body))


    story.append(
        Paragraph(
            "Blockchain certification",
            section,
        )
    )

    chain_rows = [
        [
            "Network",
            _safe(network),
        ],
        [
            "Proof contract",
            Paragraph(
                _safe(proof_contract),
                hash_style,
            ),
        ],
        [
            "Transaction ID",
            Paragraph(
                _safe(tx_hash),
                hash_style,
            ),
        ],
        [
            "Block",
            _safe(block_number),
        ],
        [
            "Blockchain timestamp",
            timestamp,
        ],
        [
            "Publisher",
            Paragraph(
                _safe(publisher_address),
                hash_style,
            ),
        ],
    ]

    if publisher_identity and publisher_identity.get("orcid"):
        orcid_id = publisher_identity["orcid"]
        if publisher_identity.get("status") == "verified":
            detail = (
                "Linked to the publishing address; confirmed with ORCID's published "
                f"keys on {str(publisher_identity.get('verified_at', ''))[:10]}."
            )
        else:
            detail = (
                "Linked to the publishing address; NOT confirmed with ORCID "
                "when this certificate was generated."
            )
        chain_rows.append(
            [
                "Publisher identity",
                Paragraph(
                    f'ORCID iD <a href="https://orcid.org/{orcid_id}" color="#1f4e8c">'
                    f"{orcid_id}</a><br/>"
                    f'<font size="6.5" color="#5f6b7a">{detail}</font>',
                    body,
                ),
            ]
        )

    chain_table = Table(
        chain_rows,
        colWidths=[
            43 * mm,
            82 * mm,
        ],
    )

    chain_table.setStyle(
        TableStyle(
            [
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.35,
                    colors.HexColor("#c8d0d8"),
                ),
                (
                    "BACKGROUND",
                    (0, 0),
                    (0, -1),
                    colors.HexColor("#f6f8fa"),
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (0, -1),
                    "Helvetica-Bold",
                ),
                (
                    "FONTSIZE",
                    (0, 0),
                    (-1, -1),
                    7.5,
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
            ]
        )
    )


    if qr_data:
        qr = _make_qr(
            qr_data,
            size=28 * mm,
        )
    else:
        qr = Spacer(
            28 * mm,
            28 * mm,
        )

    qr_block = Table(
        [
            [qr],
            [
                Paragraph(
                    qr_caption,
                    small,
                )
            ],
        ],
        colWidths=[
            35 * mm,
        ],
    )

    qr_block.setStyle(
        TableStyle(
            [
                (
                    "ALIGN",
                    (0, 0),
                    (-1, -1),
                    "CENTER",
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    0,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    0,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    0,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    1,
                ),
            ]
        )
    )

    cert_table = Table(
        [
            [
                chain_table,
                qr_block,
            ]
        ],
        colWidths=[
            125 * mm,
            35 * mm,
        ],
    )

    cert_table.setStyle(
        TableStyle(
            [
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    0,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    0,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    0,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    0,
                ),
            ]
        )
    )

    story.append(cert_table)


    if data_origin:
        story.append(PageBreak())
        story += raw_data_annex(data_origin, _component_sha(components, "data_origin"), bundle_hash,
                                section=section, body=body, hash_style=hash_style, title=title)

    doc.build(story)

    return output_path


def _component_sha(components, component_id):
    for c in components or []:
        if c.get("id") == component_id:
            return c.get("sha256")
    return None


def raw_data_tables(origin):
    """Content of the annex as plain data: intro, raw files, reference, code, derived files, verification command."""
    schema = str(origin.get("schema", ""))
    out = {"files": [], "reference": [], "derived": []}
    if schema.startswith("omicchain.matrix"):
        src = origin.get("source", {})
        out["intro"] = ("The analysis started from a count matrix provided by the user. The original file is "
                        "identified by its SHA-256; the alignment and counting that produced it are not covered "
                        "by this certificate.")
        out["files_header"] = ["File", "Content", "SHA-256"]
        out["files"].append([src.get("original_file", ""), f"{src.get('genes')} genes x {src.get('samples')} samples",
                             src.get("original_sha256", "")])
        out["check"] = "--from-raw --matrix <original count matrix>"
        out["check_label"] = "From the original count matrix (seconds)"
    else:
        inputs = origin.get("inputs", [])
        local = any(str(i.get("md5_source", "")).startswith("local") for i in inputs)
        acc = (origin.get("study") or {}).get("accession", "")
        if local:
            out["intro"] = ("FASTQ files provided by the user. Their md5 and SHA-256 were computed by OmicChain when the "
                            "files were registered and checked again before alignment. Others can check them only if "
                            "the files are shared (deposited in SRA / ENA, Zenodo...).")
        else:
            out["intro"] = (f"FASTQ files of the public study {acc}, downloaded from the ENA. Their md5 is the one "
                            "published by the ENA, checked at download and again before alignment: anyone can "
                            "download these runs again and compare.")
        out["files_header"] = ["Sample", "Run", "File", "Checksum"]
        for i in inputs:
            shas = i.get("sha256") or []
            for k, name in enumerate(i.get("fastq", [])):
                md5 = (i.get("md5") or [""] * (k + 1))[k]
                checksum = f"md5 {md5}" + (f"\nSHA-256 {shas[k]}" if k < len(shas) and shas[k] else "")
                out["files"].append([i.get("sample", ""), i.get("run", ""), name, checksum])
        ref = origin.get("reference") or {}
        if ref:
            out["reference"] = [
                ["Annotation", f"GENCODE v{ref.get('gencode_release')} (GRCh38 primary assembly)"],
                ["Genome (FASTA) SHA-256", ref.get("genome_sha256", "")],
                ["Annotation (GTF) SHA-256", ref.get("gtf_sha256", "")],
                [f"Rsubread {ref.get('index_type')} index fingerprint", ref.get("index_fingerprint", "")],
                ["Index built with", f"Rsubread {ref.get('index_rsubread_version')} ({ref.get('index_origin')})"],
            ]
        study = origin.get("study") or {}
        for key, label in (("fetch_manifest_sha256", "Download manifest"), ("samples_tsv_sha256", "Sample table (registered)" if local else "Sample table (GEO / ENA)"),
                           ("samples_table_sha256", f"Sample table used ({study.get('samples_table', '')})")):
            if study.get(key):
                out["derived"].append([label, study[key]])
        out["check_label"] = "From the raw FASTQ files (hours: alignment and counting again)"
        out["check"] = "--from-raw --fastq-dir <folder with the FASTQ files>" if local else \
            "--from-raw   (the FASTQ files are downloaded again from the ENA)"
    ann = origin.get("user_annotations") or (origin.get("study") or {}).get("user_annotations") or {}
    if ann.get("annotations_json_sha256"):
        out["derived"].append(["Sample annotations (user)", ann["annotations_json_sha256"]])
    if ann.get("samples_annotated_sha256"):
        out["derived"].append(["Annotated sample table (user)", ann["samples_annotated_sha256"]])
    for sheet in ann.get("imported_sheets", []):
        out["derived"].append([f"Imported sample sheet {sheet.get('file', '')}", sheet.get("sha256", "")])
    labels = {"counts_by_gene_id.tsv": "Counts per gene_id", "expression_input.tsv": "Expression table analysed",
              "metadata.tsv": "Sample metadata produced", "qc_summary.tsv": "Quality control per run"}
    for name, digest in (origin.get("outputs") or {}).items():
        out["derived"].append([f"{labels.get(name, name)} ({name})", digest])
    tool = origin.get("tool") or {}
    out["code"] = [[name, digest] for name, digest in (tool.get("code_sha256") or {}).items() if digest]
    out["code_version"] = f"{tool.get('name', 'OmicChain')} {tool.get('version', '')}".strip()
    return out


def raw_data_annex(origin, origin_sha256, bundle_hash, *, section, body, hash_style, title):
    """Annex pages: every raw file with its checksum, the reference, the upstream code and the derived files."""
    t = raw_data_tables(origin)
    mono = ParagraphStyle("AnnexMono", parent=hash_style, fontSize=6.3, leading=7.6)
    cell = ParagraphStyle("AnnexCell", parent=body, fontSize=7, leading=8.6)
    head = ParagraphStyle("AnnexHead", parent=cell, fontName="Helvetica-Bold")
    style = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c8d0d8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])
    header_style = TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6"))])
    label_style = TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f6f8fa"))])

    def esc(x):
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")

    story = [Paragraph("ANNEX — CERTIFIED RAW DATA", ParagraphStyle("AnnexTitle", parent=title, fontSize=13, leading=16)),
             Paragraph(f"Composite fingerprint {esc(bundle_hash)}", ParagraphStyle("AnnexSub", parent=mono, alignment=TA_CENTER)),
             Spacer(1, 3 * mm),
             Paragraph(
                 "These checksums are recorded in <b>data_origin.json</b>, a certified component of the fingerprint"
                 + (f" (SHA-256 <font face='Courier'>{esc(origin_sha256)}</font>)" if origin_sha256 else "")
                 + ". The analysis was only certified because the expression table analysed is exactly the one this "
                   "data origin describes. A single byte changed in any file below gives another checksum, which no "
                   "longer matches the certificate.", body),
             Spacer(1, 1.5 * mm), Paragraph(esc(t["intro"]), body)]

    story.append(Paragraph("Raw data files" + (f" ({len(t['files'])})" if len(t["files"]) > 1 else ""), section))
    widths = [30 * mm, 50 * mm, 94 * mm] if len(t["files_header"]) == 3 else [20 * mm, 22 * mm, 47 * mm, 85 * mm]
    rows = [[Paragraph(h, head) for h in t["files_header"]]]
    for r in t["files"]:
        rows.append([Paragraph(esc(v), cell) for v in r[:-1]] + [Paragraph(esc(r[-1]), mono)])
    tbl = Table(rows, colWidths=widths, repeatRows=1)
    tbl.setStyle(style); tbl.setStyle(header_style)
    story.append(tbl)

    for heading, data in (("Reference used for alignment and counting", t["reference"]),
                          (f"Code that built the expression table ({t['code_version']}; SHA-256, line endings "
                           "normalised)", t["code"]),
                          ("Intermediate and derived files", t["derived"])):
        if data:
            story.append(Paragraph(heading, section))
            tbl = Table([[Paragraph(esc(k), cell), Paragraph(esc(v), mono)] for k, v in data],
                        colWidths=[62 * mm, 112 * mm])
            tbl.setStyle(style); tbl.setStyle(label_style)
            story.append(tbl)

    story.append(Paragraph("How to check", section))
    story.append(Paragraph(
        "From the expression table (minutes): <font face='Courier'>python omicchain_verify.py omicchain-crate.zip "
        f"--expected {esc(bundle_hash)}</font>. {esc(t['check_label'])}: add "
        f"<font face='Courier'>{esc(t['check'])}</font>. The same checks are in the Verify tab of OmicChain "
        "(\"Everything, from the raw data\").", body))
    return story

def data_origin_rows(origin):
    """Rows [label, text] describing the certified data origin (public study, user FASTQ files or count matrix)."""
    if not origin:
        return []
    rows = []
    schema = origin.get("schema", "")
    ann = origin.get("user_annotations") or (origin.get("study") or {}).get("user_annotations")
    if schema.startswith("omicchain.matrix"):
        src = origin.get("source", {})
        rows.append(["Source", f"Count matrix provided by the user: {src.get('original_file')} "
                               f"({src.get('genes')} genes x {src.get('samples')} samples, "
                               f"SHA-256 {str(src.get('original_sha256'))[:16]}...)"
                               + ("; non-integer values rounded" if src.get("rounded_values") else "")])
    else:
        study = origin.get("study", {})
        inputs = origin.get("inputs", [])
        n_files = sum(len(i.get("fastq", [])) for i in inputs)
        local = any(str(i.get("md5_source", "")).startswith("local") for i in inputs)
        acc = study.get("accession", "")
        if local:
            rows.append(["Source", f"FASTQ files provided by the user ({acc}): {n_files} file(s), "
                                   f"{len(inputs)} sequencing unit(s); md5 and SHA-256 recorded, "
                                   "md5 checked again before alignment"])
        else:
            rows.append(["Source", f"Public study {acc} (GEO / SRA, FASTQ from ENA): {n_files} file(s), "
                                   f"{len(inputs)} run(s); md5 from ENA checked at download and again "
                                   "before alignment"])
        ref = origin.get("reference", {})
        if ref:
            rows.append(["Reference", f"GENCODE v{ref.get('gencode_release')} (GRCh38 primary assembly); "
                                      f"Rsubread {ref.get('index_type')} index, fingerprint "
                                      f"{str(ref.get('index_fingerprint'))[:16]}..."])
        al, ct = origin.get("alignment", {}), origin.get("counting", {})
        if al:
            rows.append(["Alignment and counting",
                         f"Rsubread {al.get('rsubread_version')} align ({al.get('type')}, "
                         f"{'paired-end' if al.get('paired_end') else 'single-end'}) + featureCounts "
                         f"(exons, gene_id; strandSpecific = {ct.get('strand_specific')}, {ct.get('strand_mode')}); "
                         f"{ct.get('gene_symbols')} genes"])
    d = origin.get("design", {})
    if d:
        rows.append(["Comparison", f"{d.get('condition_column')}: {d.get('test_level')} vs "
                                   f"{d.get('reference_level')} ({len(d.get('samples', []))} samples)"])
    if ann:
        bits = [f"columns added: {', '.join(ann.get('user_columns', []))}"]
        if ann.get("rules"):
            bits.append(f"{len(ann['rules'])} keyword rule(s)")
        if ann.get("imported_sheets"):
            bits.append("sample sheet(s): " + ", ".join(s.get("file", "") for s in ann["imported_sheets"]))
        if ann.get("edited_by_hand"):
            bits.append("edited by hand")
        rows.append(["Sample annotation (user)", "; ".join(bits)])
    warns = origin.get("warnings", [])
    if warns:
        rows.append(["Warnings", f"{len(warns)}: " + warns[0] + (" ..." if len(warns) > 1 else "")])
    return rows
