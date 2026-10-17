"""Minimal .docx writer (standard library only): headings, paragraphs, bullets, tables, one PNG."""
import struct
import zipfile
from xml.sax.saxutils import escape

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')


def _run(text, bold=False, italic=False, mono=False, size=None):
    pr = ""
    if bold: pr += "<w:b/>"
    if italic: pr += "<w:i/>"
    if mono: pr += '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
    if size: pr += f'<w:sz w:val="{size}"/>'
    return f'<w:r><w:rPr>{pr}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _inline(text, **kw):
    """**bold** and `code` markers inside text."""
    out, i = [], 0
    while i < len(text):
        if text.startswith("**", i):
            j = text.find("**", i + 2)
            if j > 0:
                out.append(_run(text[i + 2:j], bold=True, **kw)); i = j + 2; continue
        if text[i] == "`":
            j = text.find("`", i + 1)
            if j > 0:
                out.append(_run(text[i + 1:j], mono=True)); i = j + 1; continue
        j = i
        while j < len(text) and not text.startswith("**", j) and text[j] != "`":
            j += 1
        if j == i:
            j = i + 1
        out.append(_run(text[i:j], **kw)); i = j
    return "".join(out)


class Doc:
    def __init__(self):
        self.body, self.image = [], None

    def heading(self, text, level=1):
        self.body.append(f'<w:p><w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>{_run(text)}</w:p>')

    def title(self, text, size=40):
        self.body.append(f'<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:before="1800" w:after="240"/></w:pPr>{_run(text, bold=True, size=size)}</w:p>')

    def center(self, text, size=24, italic=False):
        self.body.append(f'<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:after="120"/></w:pPr>{_run(text, size=size, italic=italic)}</w:p>')

    def para(self, text, italic=False):
        self.body.append(f'<w:p><w:pPr><w:jc w:val="both"/></w:pPr>{_inline(text, italic=italic)}</w:p>')

    def bullet(self, text):
        self.body.append('<w:p><w:pPr><w:ind w:left="540" w:hanging="270"/><w:spacing w:after="60"/></w:pPr>'
                         + _run("•  ") + _inline(text) + "</w:p>")

    def code(self, text):
        for line in text.split("\n"):
            self.body.append('<w:p><w:pPr><w:spacing w:after="0"/><w:ind w:left="360"/></w:pPr>'
                             + _run(line, mono=True, size=18) + "</w:p>")
        self.body.append('<w:p/>')

    def caption(self, text):
        self.body.append(f'<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:after="200"/></w:pPr>{_run(text, italic=True, size=20)}</w:p>')

    def pagebreak(self):
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    def table(self, rows, widths=None):
        n = len(rows[0])
        w = widths or [int(9000 / n)] * n
        x = ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>'
             + "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
                       for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
             + '</w:tblBorders><w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'
             + "".join(f'<w:gridCol w:w="{c}"/>' for c in w) + "</w:tblGrid>")
        for ri, row in enumerate(rows):
            x += "<w:tr>"
            for ci, cell in enumerate(row):
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/>' if ri == 0 else ""
                x += (f'<w:tc><w:tcPr><w:tcW w:w="{w[ci]}" w:type="dxa"/>{shade}</w:tcPr><w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
                      + _inline(str(cell), bold=(ri == 0), size=19) + "</w:p></w:tc>")
            x += "</w:tr>"
        self.body.append(x + "</w:tbl><w:p/>")

    def picture(self, path, width_in=6.0):
        data = open(path, "rb").read()
        wpx, hpx = struct.unpack(">II", data[16:24])
        cx = int(width_in * 914400)
        cy = int(cx * hpx / wpx)
        self.image = data
        self.body.append(
            '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="1" name="Figure"/>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
            '<pic:nvPicPr><pic:cNvPr id="0" name="fig.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="rId10"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')

    def save(self, path):
        styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/><w:sz w:val="24"/></w:rPr></w:rPrDefault>'
                  '<w:pPrDefault><w:pPr><w:spacing w:after="140" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
                  '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
                  + "".join(f'<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="heading {i}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
                            f'<w:pPr><w:keepNext/><w:spacing w:before="{360 - 60 * i}" w:after="120"/><w:outlineLvl w:val="{i - 1}"/></w:pPr>'
                            f'<w:rPr><w:b/><w:sz w:val="{[32, 28, 24][i - 1]}"/></w:rPr></w:style>' for i in (1, 2, 3))
                  + "</w:styles>")
        doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>' + "".join(self.body)
               + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1300" w:bottom="1440" w:left="1300" w:header="708" w:footer="708"/></w:sectPr></w:body></w:document>')
        rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                + ('<Relationship Id="rId10" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/fig.png"/>' if self.image else "")
                + "</Relationships>")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                       '<Default Extension="png" ContentType="image/png"/>'
                       '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                       '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
            z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr("word/document.xml", doc)
            z.writestr("word/styles.xml", styles)
            z.writestr("word/_rels/document.xml.rels", rels)
            if self.image:
                z.writestr("word/media/fig.png", self.image)
