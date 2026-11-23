"""Construye docs/informe_final/SIRA_informe_final.docx (python-docx).

Se va llenando sección por sección. Convenciones:
  - Tablas: leyenda "Tabla N." arriba; figuras: leyenda "Figura N." abajo; ambas citadas en el texto.
  - Todo lo que no es nuestro va con referencia IEEE [n], numerada en orden de aparición (lista REFS).
  - Ecuaciones: MathML → OMML con el XSLT de Office (ecuaciones nativas de Word).
Correr con el Python global (tiene python-docx):  python docs/informe_final/construir_informe.py
"""
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from lxml import etree

AQUI = Path(__file__).resolve().parent
FIGS = AQUI / "figs"
SALIDA = AQUI / "SIRA_informe_final.docx"
XSL = Path(r"C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL")
FUENTE, TAM = "Arial", 10.5
AZUL_OSC, GRIS_CAB = RGBColor(0x1F, 0x3A, 0x5F), "DCE6F0"

# ---------------------------------------------------------------- referencias IEEE (orden de aparición)
REFS = [
    ("martinez", 'R. R. Martínez-Vega, S. Ouazaa, T. De Swaef, N. Chaali, S. Garré, C. I. Jaramillo-Barrios y '
                 'L. P. Moreno Fonseca, "Exploring non-conventional irrigation methods and drone-based monitoring to '
                 'enhance water use efficiency: a case of rice cultivation in Colombia," Irrigation Science, vol. 43, '
                 'pp. 1615–1633, 2025, doi: 10.1007/s00271-025-01025-w.'),
    ("irri", 'International Rice Research Institute (IRRI), "Every drop counts," Rice Today, 27 jul. 2009. [En línea]. '
             'Disponible: https://ricetoday.irri.org/every-drop-counts/'),
    ("agronegocios", 'S. Ibañez Pita, "¿Por qué se importa alrededor de 16% del arroz blanco si el país es productor?," '
                     'Agronegocios, 8 oct. 2026. [En línea]. Disponible: https://www.agronegocios.co/agricultura/'
                     'por-que-se-importa-alrededor-de-16-del-arroz-blanco-si-el-pais-es-productor-4499015'),
    ("larepublica", 'S. Ibañez Pita, "Nueve de cada 10 toneladas de arroz que se importan provienen de Estados Unidos," '
                    'La República, 17 oct. 2026. [En línea]. Disponible: https://www.larepublica.co/economia/'
                    'nueve-de-cada-10-toneladas-de-arroz-que-se-importan-provienen-de-estados-unidos-4504928'),
    ("portafolio", 'D. K. Rodríguez T., "A 38 meses del arancel cero a EE. UU., el arroz enfrenta una tríada de retos que '
                   'ponen a prueba su competitividad," Portafolio, 7 oct. 2026. [En línea]. Disponible: '
                   'https://www.portafolio.co/economia/agro/a-38-meses-del-arancel-cero-el-arroz-colombiano-enfrenta-'
                   'tres-frentes-que-ponen-a-prueba-su-competitividad-504023'),
    ("wikico", '"Colombia," Wikipedia. Consultado: 22 nov. 2026. [En línea]. Disponible: https://en.wikipedia.org/wiki/Colombia'),
    ("wikinl", '"Netherlands," Wikipedia. Consultado: 22 nov. 2026. [En línea]. Disponible: https://en.wikipedia.org/wiki/Netherlands'),
    ("cbs", 'Statistics Netherlands (CBS), "Value of agricultural exports up by nearly 5 percent in 2024," 17 ene. 2025. '
            '[En línea]. Disponible: https://www.cbs.nl/en-gb/news/2025/03/value-of-agricultural-exports-up-by-nearly-5-percent-in-2024'),
    ("nederhoff", 'E. M. Nederhoff y C. Stanghellini, "Water use efficiency of tomatoes — in greenhouses and hydroponics," '
                  'Practical Hydroponics & Greenhouses, no. 115, pp. 52–59, nov./dic. 2010.'),
    ("naturalearth", 'Natural Earth, "Admin 0 – Countries, 1:110m," dominio público. Consultado: 22 nov. 2026. [En línea]. '
                     'Disponible: https://www.naturalearthdata.com/'),
    ("fao56", 'R. G. Allen, L. S. Pereira, D. Raes y M. Smith, Crop Evapotranspiration — Guidelines for Computing Crop '
              'Water Requirements, FAO Irrigation and Drainage Paper 56. Roma, Italia: FAO, 1998.'),
    # ---- sección 3 (orden de aparición)
    ("dane", 'DANE y Fedearroz–FNA, "Encuesta Nacional de Arroz Mecanizado (ENAM), segundo semestre 2025," Boletín técnico, '
             'Bogotá, Colombia, feb. 2026. [En línea]. Disponible: https://www.dane.gov.co/files/operaciones/ENAM/bol-ENAM-IIsem2025.pdf'),
    ("ideam", 'IDEAM, Estudio Nacional del Agua 2018. Bogotá, Colombia: IDEAM, 2019.'),
    ("triana", 'J. C. Triana-Martinez, A. M. Álvarez-Meza, J. Gil-González, T. De Swaef y J. A. Fernandez-Gallego, "Crop water '
               'status analysis from complex agricultural data using UMAP-based local biplot," Remote Sensing, vol. 16, no. 15, '
               'p. 2854, 2024, doi: 10.3390/rs16152854.'),
    ("funk", 'C. Funk et al., "The climate hazards infrared precipitation with stations—a new environmental record for '
             'monitoring extremes," Scientific Data, vol. 2, art. 150066, 2015, doi: 10.1038/sdata.2015.66.'),
    ("power", 'NASA Langley Research Center, "POWER: Prediction Of Worldwide Energy Resources," [En línea]. Disponible: '
              'https://power.larc.nasa.gov/'),
    ("drusch", 'M. Drusch et al., "Sentinel-2: ESA\'s optical high-resolution mission for GMES operational services," Remote '
               'Sensing of Environment, vol. 120, pp. 25–36, 2012, doi: 10.1016/j.rse.2011.11.026.'),
    ("claverie", 'M. Claverie et al., "The Harmonized Landsat and Sentinel-2 surface reflectance data set," Remote Sensing of '
                 'Environment, vol. 219, pp. 145–161, 2018, doi: 10.1016/j.rse.2018.09.002.'),
    ("gao", 'B.-C. Gao, "NDWI—A normalized difference water index for remote sensing of vegetation liquid water from space," '
            'Remote Sensing of Environment, vol. 58, no. 3, pp. 257–266, 1996, doi: 10.1016/S0034-4257(96)00067-3.'),
    ("mcfeeters", 'S. K. McFeeters, "The use of the Normalized Difference Water Index (NDWI) in the delineation of open water '
                  'features," International Journal of Remote Sensing, vol. 17, no. 7, pp. 1425–1432, 1996, '
                  'doi: 10.1080/01431169608948714.'),
    ("nasaeo", 'J. Weier y D. Herring, "Measuring vegetation (NDVI & EVI)," NASA Earth Observatory, 30 ago. 2000. [En línea]. '
               'Disponible: https://science.nasa.gov/earth/earth-observatory/measuring-vegetation-ndvi-evi'),
    ("monteith", 'J. L. Monteith, "Solar radiation and productivity in tropical ecosystems," Journal of Applied Ecology, '
                 'vol. 9, no. 3, pp. 747–766, 1972.'),
    ("running", 'S. W. Running, R. R. Nemani, F. A. Heinsch, M. Zhao, M. Reeves y H. Hashimoto, "A continuous satellite-derived '
                'measure of global terrestrial primary production," BioScience, vol. 54, no. 6, pp. 547–560, jun. 2004, '
                'doi: 10.1641/0006-3568(2004)054[0547:ACSMOG]2.0.CO;2.'),
    # ---- sección 3.3
    ("dm", 'F. X. Diebold y R. S. Mariano, "Comparing predictive accuracy," Journal of Business & Economic Statistics, '
           'vol. 13, no. 3, pp. 253–263, 1995.'),
    ("prithvi", 'D. Szwarcman et al., "Prithvi-EO-2.0: A versatile multi-temporal foundation model for Earth observation '
                'applications," arXiv:2412.02732, 2024.'),
    ("breiman", 'L. Breiman, "Random forests," Machine Learning, vol. 45, no. 1, pp. 5–32, 2001, '
                'doi: 10.1023/A:1010933404324.'),
    ("roberts", 'D. R. Roberts et al., "Cross-validation strategies for data with temporal, spatial, hierarchical, or '
                'phylogenetic structure," Ecography, vol. 40, no. 8, pp. 913–929, 2017, doi: 10.1111/ecog.02881.'),
    ("molod", 'A. Molod et al., "GEOS-S2S version 2: The GMAO high-resolution coupled model and assimilation system for '
              'seasonal prediction," Journal of Geophysical Research: Atmospheres, vol. 125, no. 5, art. e2019JD031767, '
              '2020, doi: 10.1029/2019JD031767.'),
]
NUM = {k: i + 1 for i, (k, _) in enumerate(REFS)}


def c(*claves):
    """Cita IEEE: c('martinez') → '[1]'; varias → '[1], [3]'."""
    return ", ".join(f"[{NUM[k]}]" for k in claves)


# ---------------------------------------------------------------- utilidades de formato
doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Inches(8.5), Inches(11)
for lado in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
    setattr(sec, lado, Inches(1))
ANCHO = Inches(6.5)

st = doc.styles["Normal"]
st.font.name, st.font.size = FUENTE, Pt(TAM)
st.element.rPr.rFonts.set(qn("w:eastAsia"), FUENTE)
st.paragraph_format.space_after = Pt(6)
st.paragraph_format.line_spacing = 1.15
for nivel, tam in ((1, 15), (2, 12.5), (3, 11)):
    h = doc.styles[f"Heading {nivel}"]
    h.font.name, h.font.size, h.font.bold, h.font.color.rgb = FUENTE, Pt(tam), True, AZUL_OSC
    h.element.rPr.rFonts.set(qn("w:asciiTheme"), "")  # que no use la fuente del tema
    h.element.rPr.rFonts.set(qn("w:hAnsi"), FUENTE)
    h.element.rPr.rFonts.set(qn("w:ascii"), FUENTE)
    h.paragraph_format.space_before = Pt(14 if nivel == 1 else 10)
    h.paragraph_format.space_after = Pt(6)
    h.paragraph_format.keep_with_next = True

CONT = {"tabla": 0, "figura": 0, "ec": 0}


def parrafo(texto="", negrita=False, cursiva=False, alinear=WD_ALIGN_PARAGRAPH.JUSTIFY, tam=None, color=None, despues=None):
    p = doc.add_paragraph()
    p.alignment = alinear
    if texto:
        _runs(p, texto, negrita, cursiva, tam, color)
    if despues is not None:
        p.paragraph_format.space_after = Pt(despues)
    return p


def _runs(p, texto, negrita=False, cursiva=False, tam=None, color=None):
    """Texto con **negrita** opcional marcada entre dobles asteriscos."""
    for i, trozo in enumerate(texto.split("**")):
        if not trozo:
            continue
        r = p.add_run(trozo)
        r.bold = negrita or (i % 2 == 1)
        r.italic = cursiva
        if tam:
            r.font.size = Pt(tam)
        if color:
            r.font.color.rgb = color


def pendiente(texto):
    parrafo(f"[Por redactar] {texto}", cursiva=True, color=RGBColor(0x88, 0x87, 0x80))


def leyenda(tipo, texto):
    CONT[tipo] += 1
    n = CONT[tipo]
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if tipo == "figura" else WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_after = Pt(10 if tipo == "figura" else 4)
    p.paragraph_format.keep_with_next = tipo == "tabla"
    r = p.add_run(f"{'Tabla' if tipo == 'tabla' else 'Figura'} {n}. ")
    r.bold, r.font.size = True, Pt(9.5)
    r2 = p.add_run(texto)
    r2.font.size = Pt(9.5)
    return n


def figura(archivo, texto, ancho=6.3):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(FIGS / archivo), width=Inches(ancho))
    return leyenda("figura", texto)


def _sombrear(celda, color):
    tcPr = celda._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"), shd.set(qn("w:color"), "auto"), shd.set(qn("w:fill"), color)
    tcPr.append(shd)


def tabla(texto_leyenda, encabezado, filas, anchos, nota=None, alinear_num=True):
    """Leyenda arriba, tabla con encabezado sombreado y nota de fuente abajo."""
    n = leyenda("tabla", texto_leyenda)
    t = doc.add_table(rows=1 + len(filas), cols=len(encabezado))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for j, w in enumerate(anchos):
        for fila in t.rows:
            fila.cells[j].width = Inches(w)
    for i, fila_datos in enumerate([encabezado] + filas):
        for j, val in enumerate(fila_datos):
            celda = t.rows[i].cells[j]
            celda.text = ""
            p = celda.paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = 1.0
            num = alinear_num and j > 0 and i > 0 and any(ch.isdigit() for ch in str(val)) and len(str(val)) < 22
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if (i == 0 or num) else WD_ALIGN_PARAGRAPH.LEFT
            _runs(p, str(val), negrita=(i == 0), tam=9)
            if i == 0:
                _sombrear(celda, GRIS_CAB)
            if i < len(filas):  # que la tabla no se parta entre páginas
                for pp in celda.paragraphs:
                    pp.paragraph_format.keep_with_next = True
    q = parrafo(nota or "", tam=8.5, alinear=WD_ALIGN_PARAGRAPH.LEFT, despues=10)
    q.paragraph_format.space_before = Pt(2)
    if not nota:
        q.paragraph_format.line_spacing = Pt(4)
    return n


_xslt = etree.XSLT(etree.parse(str(XSL)))


def _sin_bordes(t):
    tblPr = t._tbl.tblPr
    b = OxmlElement("w:tblBorders")
    for lado in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{lado}")
        e.set(qn("w:val"), "nil")
        b.append(e)
    tblPr.append(b)


def ecuacion(mathml):
    """Ecuación en modo display (OMML nativo, oMathPara) centrada, con su número a la derecha."""
    CONT["ec"] += 1
    t = doc.add_table(rows=1, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    _sin_bordes(t)
    for j, w in enumerate((5.9, 0.6)):
        t.rows[0].cells[j].width = Inches(w)
    mml = etree.fromstring(f'<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">{mathml}</math>')
    p = t.rows[0].cells[0].paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(2), Pt(2)
    p._p.append(_xslt(mml).getroot())
    q = t.rows[0].cells[1].paragraphs[0]
    q.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    q.add_run(f"({CONT['ec']})")
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    for celda in t.rows[0].cells:
        celda.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    return CONT["ec"]


ESP = '<mtext> </mtext>'


def txt(s):
    return f"<mtext>{s}</mtext>"


def mi(x):
    return f"<mi>{x}</mi>"


def sub(base, s):
    return f"<msub><mi>{base}</mi><mi>{s}</mi></msub>"


def mo(x):
    return f"<mo>{x}</mo>"


def mn(x):
    return f"<mn>{x}</mn>"


def frac(a, b):
    return f"<mfrac><mrow>{a}</mrow><mrow>{b}</mrow></mfrac>"


# ================================================================ PORTADA
for _ in range(5):
    parrafo("")
parrafo("SIRA", negrita=True, alinear=WD_ALIGN_PARAGRAPH.CENTER, tam=30, color=AZUL_OSC, despues=2)
parrafo("Sistema Inteligente de Riego para Arroz", alinear=WD_ALIGN_PARAGRAPH.CENTER, tam=16, color=AZUL_OSC, despues=4)
parrafo("Semáforo de humedad del suelo por píxel, de hoy a 30 días, con satélite, pronóstico climático y "
        "aprendizaje automático", cursiva=True, alinear=WD_ALIGN_PARAGRAPH.CENTER, tam=11.5, despues=40)
parrafo("Informe final · Proyecto Integrador", negrita=True, alinear=WD_ALIGN_PARAGRAPH.CENTER, tam=12)
parrafo("Equipo 10", alinear=WD_ALIGN_PARAGRAPH.CENTER, tam=12)
parrafo("[Integrante 1] · [Integrante 2] · [Integrante 3] · [Integrante 4] · [Integrante 5]",
        alinear=WD_ALIGN_PARAGRAPH.CENTER, tam=11, color=RGBColor(0x88, 0x87, 0x80), despues=40)
parrafo("Universidad EAFIT", alinear=WD_ALIGN_PARAGRAPH.CENTER, tam=11.5)
parrafo("[Programa / curso] · 2026-2", alinear=WD_ALIGN_PARAGRAPH.CENTER, tam=11, color=RGBColor(0x88, 0x87, 0x80))
parrafo("Medellín, 23 de noviembre de 2026", alinear=WD_ALIGN_PARAGRAPH.CENTER, tam=11)
doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

# ================================================================ 1. INTRODUCCIÓN
doc.add_heading("1. Introducción", 1)
parrafo(f"El arroz es uno de los cultivos que más agua consume en el mundo, y en el Tolima un solo ciclo puede gastar "
        f"20 000 m³ por hectárea {c('martinez')}. La mayoría de los arroceros riega por costumbre: inunda el lote y vuelve a "
        f"regar cuando le parece, sin saber cuánta agua tiene de verdad el suelo. El experimento de Agrosavia en Nataima "
        f"mostró que hay otra forma, porque las técnicas tecnificadas ahorraron entre 48 % y 65 % del agua sin perder "
        f"cosecha {c('martinez')}. El problema es que para programarlas hay que mirar el suelo todos los días, y eso no "
        f"escala. Al mismo tiempo, el arroz colombiano se prepara para competir sin arancel con el de Estados Unidos "
        f"(sección 2.2), y cada metro cúbico de agua de más es costo de más.")
parrafo("**SIRA, Sistema Inteligente de Riego para Arroz,** busca reemplazar esa inspección diaria por información "
        "gratuita de satélite y de pronóstico del clima. Para cada píxel de 10 m de un cultivo de arroz, y para cada uno de "
        "los próximos 30 días, SIRA dice si el suelo tendrá humedad suficiente, media o baja, cuántos litros por metro "
        "cuadrado le faltan y en cuántos días llegará a la humedad baja si no se riega. Lo entrega en una app para el "
        "agricultor y en un tablero para el agrónomo.")
parrafo("Para lograrlo, SIRA combina dos modelos de aprendizaje automático con el balance hídrico de la FAO. Un modelo "
        "predictor (Random Forest) pronostica el verdor y la humedad de la hoja con imágenes de Sentinel-2 y Landsat. Un "
        "modelo corrector (ARIMAX), entrenado con 198 mediciones de humedad del suelo de Nataima, estima la humedad de "
        "hoy. El balance FAO-56 propaga ese estado 30 días con la lluvia y la evapotranspiración que pronostica NASA "
        "(GEOS-S2S). Los dos modelos se eligieron entre varias familias con validación y una prueba sellada que se midió "
        "una sola vez. En esa prueba, el predictor explica el 79 % de la varianza del NDVI y el corrector clasifica la "
        "humedad con un AUC de 0,75.")
parrafo("El informe sigue el orden del proyecto. La sección 2 presenta el marco teórico: el agua del arroz, el contexto "
        "colombiano, la referencia de los Países Bajos, el experimento de Nataima y el balance FAO-56. La sección 3 "
        "desarrolla la metodología de los modelos, desde el análisis exploratorio hasta la evaluación y el semáforo en "
        "operación. La sección 4 describe la ingeniería de datos y la tecnología, la sección 5 la visualización en el "
        "tablero y en la app, y la sección 6 recoge las conclusiones y el trabajo futuro.")

# ================================================================ 2. MARCO TEÓRICO
doc.add_heading("2. Marco teórico", 1)
parrafo("Esta sección reúne la evidencia en la que se apoya SIRA: cuánta agua usa el arroz (2.1), la situación del "
        "arroz en Colombia (2.2), lo que muestra la experiencia de los Países Bajos (2.3), el experimento de Nataima que "
        "aporta los datos de campo (2.4) y el balance hídrico de la FAO con el que se propaga la humedad del suelo (2.5).")

# ---- 2.1
doc.add_heading("2.1 El arroz y el agua", 2)
parrafo(f"El arroz es uno de los cultivos más importantes del mundo, pero su cultivo consume hasta el 30 % del agua "
        f"dulce del planeta y hasta el 43 % del agua de riego (Nawaz et al., citado en {c('martinez')}). Producir un "
        f"kilogramo de arroz en cáscara requiere cerca de 2 500 L de agua: unos 1 400 L se van en evaporación y "
        f"transpiración, y unos 1 100 L en filtración y percolación {c('irri')}. En el Tolima, donde está el caso de "
        f"estudio, un ciclo de arroz consume en promedio 20 000 m³ por hectárea, y el distrito de riego limita la "
        f"asignación a 2 L/s por hectárea {c('martinez')}. La Tabla 1 resume estas cifras.")
tabla("Cifras del agua en el cultivo de arroz.",
      ["Indicador", "Valor", "Fuente"],
      [["Agua dulce del mundo que consume el arroz", "hasta 30 %", c("martinez")],
       ["Agua de riego del mundo que consume el arroz", "hasta 43 %", c("martinez")],
       ["Agua para 1 kg de arroz en cáscara", "≈ 2 500 L", c("irri")],
       ["  · evaporación y transpiración", "≈ 1 400 L", c("irri")],
       ["  · filtración y percolación", "≈ 1 100 L", c("irri")],
       ["Consumo por ciclo en el Tolima", "20 000 m³/ha", c("martinez")],
       ["Asignación máxima del distrito de riego", "2 L/s por ha", c("martinez")]],
      [3.6, 1.4, 1.0])

# ---- 2.2
doc.add_heading("2.2 El arroz en Colombia", 2)
parrafo(f"Colombia consume cerca de 2,21 millones de toneladas (Mt) de arroz blanco al año, y la producción nacional, "
        f"de 1,86 Mt, cubre el 84 % {c('agronegocios')}. El resto llega de afuera: en 2025 entraron 87 778 t importadas "
        f"y unas 262 000 t de contrabando {c('agronegocios')}. De lo importado, el 85,9 % viene de Estados Unidos "
        f"{c('larepublica')}, cuyo arroz quedará con arancel cero en 2030 por el TLC firmado en 2012 {c('portafolio')}. "
        f"La Figura 1 muestra esta composición y la Tabla 2 reúne las cifras del sector.")
figura("fig_arroz_colombia.png", f"Origen del arroz blanco consumido en Colombia en 2025 (izquierda) y participación "
       f"de Estados Unidos en las importaciones (derecha). Elaboración propia con datos de {c('agronegocios', 'larepublica')}.")
tabla("El arroz en Colombia: producción, consumo y comercio.",
      ["Indicador", "Valor", "Fuente"],
      [["Consumo anual de arroz blanco", "≈ 2,21 Mt", c("agronegocios")],
       ["Producción nacional", "1,86 Mt (84 % del consumo)", c("agronegocios")],
       ["Área sembrada", "711 375 ha", c("agronegocios")],
       ["Productores", "> 13 000 en 23 departamentos", c("agronegocios")],
       ["Importación legal, 2025", "87 778 t", c("agronegocios")],
       ["Contrabando, 2025", "≈ 262 000 t", c("agronegocios")],
       ["Importaciones que vienen de EE. UU.", "85,9 %", c("larepublica")],
       ["Cuota sin arancel para EE. UU. en 2029", "166 957 t", c("larepublica")],
       ["Arancel al arroz de EE. UU.", "0 % desde 2030", c("portafolio")]],
      [3.0, 2.0, 1.0])
parrafo(f"El arroz nacional compite, entonces, contra un producto que llega subsidiado {c('portafolio')}. El agua es "
        f"parte de su costo de producción: el arrocero que riega de más gasta de más. Ahí está la oportunidad de SIRA.")

# ---- 2.3
doc.add_heading("2.3 La referencia de los Países Bajos", 2)
parrafo(f"Los Países Bajos muestran lo que se gana cuando el agua se mide. Con un territorio 27,5 veces menor que el "
        f"de Colombia (41 543 km² frente a 1 141 748 km²) {c('wikico', 'wikinl')}, exportaron productos agrícolas por "
        f"128 900 millones de euros en 2024 {c('cbs')}. En sus invernaderos con hidroponía cerrada, un kilogramo de "
        f"tomate gasta 15 L de agua, frente a 100–300 L a campo abierto; los invernaderos cerrados más avanzados bajan a "
        f"4 L/kg {c('nederhoff')}. La Tabla 3 compara los sistemas y la Figura 2 lo pone en escala.")
tabla("Agua usada para producir 1 kg de tomate fresco según el sistema de cultivo.",
      ["Sistema de cultivo", "País", "L/kg"],
      [["Campo abierto, general", "Varios", "100–300"],
       ["Campo abierto con riego por goteo", "Israel", "60"],
       ["Invernadero de plástico sin calefacción", "España", "40"],
       ["Invernadero de vidrio sin calefacción", "Israel", "30"],
       ["Invernadero de vidrio avanzado, con CO₂", "Países Bajos", "22"],
       ["El anterior, con hidroponía cerrada", "Países Bajos", "15"],
       ["Invernadero cerrado", "Países Bajos", "4"]],
      [3.6, 1.4, 1.0], nota=f"Fuente: adaptada de la Tabla 2 de {c('nederhoff')}.")
figura("fig_paises_bajos_tomate.png", f"Izquierda: contornos de Colombia y de los Países Bajos a la misma escala. Derecha: agua para "
       f"producir 1 kg de tomate a campo abierto y en invernadero neerlandés con hidroponía cerrada; cada gota equivale "
       f"a 5 L (campo abierto representado con el punto medio del rango, 200 L/kg). Elaboración propia con datos de "
       f"{c('wikico', 'wikinl', 'nederhoff')}; contornos de {c('naturalearth')}.")
parrafo(f"Ninguna de estas fuentes atribuye el resultado exportador solo al agua: también pesan la tecnología, la "
        f"logística y el mercado europeo {c('cbs')}. La lección para SIRA no es copiar el invernadero, sino el principio: "
        f"regar con datos y no por costumbre.")

# ---- 2.4
doc.add_heading("2.4 El experimento de Nataima", 2)
parrafo(f"El caso de estudio es el experimento de Martínez-Vega et al. {c('martinez')} en el Centro de Investigación "
        f"Nataima de Agrosavia (El Espinal, Tolima; 4°11′29″ N, 74°57′39″ O; 323 m de altitud), en un bosque seco "
        f"tropical con 27 °C de temperatura media y 1 439 mm de lluvia al año. Durante tres temporadas (GS1: mayo–"
        f"septiembre de 2021; GS2: diciembre de 2021–abril de 2022; GS3: julio–noviembre de 2022) se compararon tres "
        f"técnicas de riego en franjas, con la variedad Fedearroz 67, sembrada de forma directa (Tabla 4). La humedad "
        f"volumétrica del suelo se midió casi a diario con un TDR (Fieldscout TDR 350) a 20 cm de profundidad. Esas "
        f"mediciones son la verdad de campo con la que se entrenó el modelo corrector de SIRA.")
tabla("Técnicas de riego evaluadas en Nataima.",
      ["Técnica", "Cómo llega el agua", "Cuándo se riega", "Consumo"],
      [["CD (distribución en cascada)", "Una entrada; el regador conduce el agua de una poza a otra",
        "Sin límite, para mantener el suelo saturado (manejo del agricultor)", "Alto"],
       ["MIRI (entradas múltiples)", "Manguera de polietileno con perforaciones hacia cada poza",
        "Según balance hídrico, entre el agua fácilmente aprovechable y la saturación", "Medio"],
       ["AWD+MIRI (secado y humedecimiento alterno)", "Igual que MIRI",
        "Cuando la superficie del suelo se ve seca; se repone una lámina de 5 cm", "Bajo"]],
      [1.45, 1.7, 2.1, 0.85], nota=f"Fuente: elaboración propia a partir de {c('martinez')}.", alinear_num=False)
parrafo(f"Las técnicas tecnificadas ahorraron mucha agua sin perder cosecha (Figura 3). Frente a CD, MIRI redujo el riego "
        f"entre 22 % y 54 %, y AWD+MIRI entre 48 % y 65 %. No hubo evidencia estadística de que la técnica cambiara el "
        f"rendimiento de grano (p = 0,46; 0,30 y 0,12 en GS1, GS2 y GS3), y por eso la productividad del agua fue mayor "
        f"con AWD+MIRI (Tabla 5) {c('martinez')}.")
figura("fig_riego_nataima.png", f"Lámina de riego aplicada por técnica y temporada en Nataima, y reducción frente a CD. "
       f"GS1 y las reducciones, de {c('martinez')}; láminas de GS2 y GS3 digitalizadas de la Fig. 4 de {c('martinez')}.",
       ancho=5.6)
tabla("Productividad del agua (kg de grano por m³ de agua, riego más lluvia efectiva) por técnica y temporada.",
      ["Temporada", "CD", "MIRI", "AWD+MIRI", "p (rendimiento)"],
      [["GS1", "0,66 ± 0,02", "1,00 ± 0,03", "1,21 ± 0,05", "0,46"],
       ["GS2", "0,99 ± 0,05", "1,14 ± 0,05", "1,44 ± 0,05", "0,30"],
       ["GS3", "0,48 ± 0,05", "0,87 ± 0,05", "1,03 ± 0,04", "0,12"]],
      [1.2, 1.2, 1.2, 1.2, 1.2], nota=f"Fuente: {c('martinez')}. La columna p es la prueba del efecto de la técnica "
                                      f"sobre el rendimiento de grano: ningún valor es significativo.")
parrafo(f"El artículo también caracterizó el suelo: franco arenoso, con capacidad de campo cercana al 30 % de humedad "
        f"volumétrica y punto de marchitez cercano al 26 % {c('martinez')}. Con 150 mm de raíz, el déficit crítico a "
        f"partir del cual la planta empieza a sufrir estrés es de 4,34 mm (Tabla 6). Estos dos valores fijan los "
        f"umbrales del semáforo de SIRA, como se muestra en la sección 2.5.")
tabla("Parámetros del suelo de Nataima y umbrales que se derivan de ellos.",
      ["Parámetro", "Valor", "Origen"],
      [["Textura", "Franco arenosa", c("martinez")],
       ["Capacidad de campo, θCC", "≈ 30 %", c("martinez")],
       ["Punto de marchitez permanente, θPMP", "≈ 26 %", c("martinez")],
       ["Agua total disponible, TAW", "0,0446 m³/m³ → 6,7 mm", c("martinez")],
       ["Profundidad de raíz, Zr", "150 mm", c("martinez")],
       ["Fracción de agotamiento sin estrés, p", "0,65", c("martinez", "fao56")],
       ["Déficit crítico (RAW = p·TAW)", "4,34 mm", c("martinez")],
       ["Umbral de humedad baja, θcrít (Ec. 6)", "27,1 %", "Ec. (6)"]],
      [3.2, 1.8, 1.0])

# ---- 2.5
doc.add_heading("2.5 Balance hídrico de la FAO (FAO-56)", 2)
parrafo(f"La guía FAO-56 {c('fao56')} es el estándar para calcular cuánta agua consume un cultivo. Parte de la "
        f"evapotranspiración de referencia, ETo: la de un pasto bien regado, que solo depende del clima. Se calcula con "
        f"la ecuación de Penman–Monteith (Ec. 1); sus símbolos están en la Tabla 7.")
ecuacion(sub("ET", "o") + mo("=") + frac(mn("0,408") + mi("Δ") + mo("(") + sub("R", "n") + mo("−") + mi("G") + mo(")")
                                         + mo("+") + mi("γ") + frac(mn("900"), mi("T") + mo("+") + mn("273")) + sub("u", "2")
                                         + mo("(") + sub("e", "s") + mo("−") + sub("e", "a") + mo(")"),
                                         mi("Δ") + mo("+") + mi("γ") + mo("(") + mn("1") + mo("+") + mn("0,34") + sub("u", "2") + mo(")")))
parrafo(f"El consumo del cultivo, ETc, escala la ETo con el coeficiente de cultivo Kc, que cambia con la etapa de "
        f"desarrollo; cuando falta agua, la planta cierra estomas y el consumo baja según el coeficiente de estrés Ks "
        f"(Ec. 2) {c('fao56')}.")
ecuacion(sub("ET", "c") + mo("=") + sub("K", "c") + mo("·") + sub("ET", "o") + mo(",") + ESP
         + "<msub><mi>ET</mi><mrow><mi>c</mi><mo>,</mo><mi>aj</mi></mrow></msub>" + mo("=") + sub("K", "s") + mo("·")
         + sub("K", "c") + mo("·") + sub("ET", "o"))
parrafo(f"El estado del agua en el suelo se lleva como un déficit en la zona de raíces, Dr: cuánta agua falta para "
        f"volver a capacidad de campo. Cada día el déficit baja con la lluvia y el riego, y sube con el consumo del "
        f"cultivo (Ec. 3) {c('fao56')}.")
ecuacion("<msub><mi>D</mi><mrow><mi>r</mi><mo>,</mo><mi>i</mi></mrow></msub>" + mo("=")
         + "<msub><mi>D</mi><mrow><mi>r</mi><mo>,</mo><mi>i</mi><mo>−</mo><mn>1</mn></mrow></msub>" + mo("−")
         + "<msub><mrow><mo>(</mo><mi>P</mi><mo>−</mo><mi>RO</mi><mo>)</mo></mrow><mi>i</mi></msub>" + mo("−") + sub("I", "i")
         + mo("−") + sub("CR", "i") + mo("+") + "<msub><mi>ET</mi><mrow><mi>c</mi><mo>,</mo><mi>i</mi></mrow></msub>"
         + mo("+") + sub("DP", "i"))
parrafo(f"El suelo solo guarda una cantidad limitada de agua útil. El agua total disponible, TAW, es la que hay entre "
        f"capacidad de campo y punto de marchitez en la profundidad de raíz; la fracción p de ella es el agua fácilmente "
        f"aprovechable, RAW, que la planta usa sin estrés (Ec. 4) {c('fao56')}.")
ecuacion(mi("TAW") + mo("=") + mn("1000") + mo("(") + sub("θ", "CC") + mo("−") + sub("θ", "PMP") + mo(")") + sub("Z", "r")
         + mo(",") + ESP + mi("RAW") + mo("=") + mi("p") + mo("·") + mi("TAW"))
parrafo(f"Mientras el déficit no pase de RAW, Ks vale 1; después cae en línea recta hasta 0 cuando se agota TAW (Ec. 5 y "
        f"Figura 4, derecha) {c('fao56')}.")
ecuacion(sub("K", "s") + mo("=") + "<mrow><mo>{</mo><mtable>"
         + "<mtr><mtd>" + mn("1") + mo(",") + ESP + txt("si ") + sub("D", "r") + mo("≤") + mi("RAW") + "</mtd></mtr>"
         + "<mtr><mtd>" + frac(mi("TAW") + mo("−") + sub("D", "r"), mo("(") + mn("1") + mo("−") + mi("p") + mo(")") + mi("TAW"))
         + mo(",") + ESP + txt("si ") + sub("D", "r") + mo(">") + mi("RAW") + "</mtd></mtr></mtable></mrow>")
parrafo(f"Con los parámetros de Nataima (Tabla 6), RAW coincide con el déficit crítico de 4,34 mm de {c('martinez')}. "
        f"Llevado a humedad volumétrica, ese déficit se resta de la capacidad de campo y da el umbral de humedad baja "
        f"(Ec. 6). Así quedan los tres estados del semáforo de SIRA (Figura 4, izquierda): suficiente desde 30 %, media "
        f"entre 27,1 % y 30 %, y baja por debajo de 27,1 %.")
ecuacion(sub("θ", "crít") + mo("=") + sub("θ", "CC") + mo("−") + frac(mi("RAW"), sub("Z", "r")) + mo("=") + mn("30")
         + mo("%") + mo("−") + frac(mn("4,34") + txt(" mm"), mn("150") + txt(" mm")) + mo("=") + mn("27,1")
         + mo("%"))
figura("fig_umbrales_semaforo.png", f"Izquierda: estados del semáforo según la humedad volumétrica del suelo. Derecha: "
       f"coeficiente de estrés hídrico Ks en función del déficit Dr, con los parámetros de Nataima. Elaboración propia "
       f"con base en {c('martinez', 'fao56')}.")
tabla("Símbolos del balance hídrico FAO-56.",
      ["Símbolo", "Significado", "Unidad"],
      [["ETo", "Evapotranspiración de referencia", "mm/día"],
       ["ETc, ETc,aj", "Evapotranspiración del cultivo, sin y con estrés", "mm/día"],
       ["Rn, G", "Radiación neta y flujo de calor del suelo", "MJ/m²·día"],
       ["T, u₂", "Temperatura media del aire y velocidad del viento a 2 m", "°C, m/s"],
       ["es − ea", "Déficit de presión de vapor", "kPa"],
       ["Δ, γ", "Pendiente de la curva de presión de vapor y constante psicrométrica", "kPa/°C"],
       ["Kc, Ks", "Coeficiente de cultivo y coeficiente de estrés hídrico", "—"],
       ["Dr", "Déficit de agua en la zona de raíces", "mm"],
       ["P, RO, I", "Lluvia, escorrentía y riego", "mm"],
       ["CR, DP", "Ascenso capilar y percolación profunda", "mm"],
       ["θCC, θPMP", "Humedad a capacidad de campo y a punto de marchitez", "m³/m³"],
       ["Zr, p", "Profundidad de raíz y fracción de agotamiento sin estrés", "m, —"],
       ["TAW, RAW", "Agua total disponible y agua fácilmente aprovechable", "mm"]],
      [1.2, 3.9, 1.1], nota=f"Fuente: {c('fao56')}.", alinear_num=False)

# ================================================================ 3–6 (por redactar)
doc.add_heading("3. Desarrollo metodológico de los modelos", 1)

# ---- 3.1
doc.add_heading("3.1 Entendimiento del problema y pregunta de negocio", 2)
parrafo(f"El arroz es el cereal que más produce Colombia, con 3,23 millones de toneladas de arroz paddy verde en 2025 "
        f"{c('dane')}, y la agricultura es el mayor usuario de agua del país, con el 43,1 % de la demanda hídrica "
        f"{c('ideam')}. Aun así, la mayoría de los arroceros riega por costumbre: en el distrito de riego del Tolima, entre "
        f"el 70 % y el 80 % son pequeños productores sin tecnología de riego avanzada {c('martinez')}, y el riego en cascada "
        f"gasta hasta tres veces más agua que las técnicas tecnificadas sin producir más grano (sección 2.4), en un mercado "
        f"que pronto competirá sin arancel con arroz subsidiado (sección 2.2). De ahí la pregunta de negocio: **¿se puede, "
        f"solo con información gratuita de satélite y de pronóstico del clima, decirle a cada lote de arroz, píxel a píxel, "
        f"si su suelo tendrá humedad suficiente, media o baja en cada uno de los próximos 30 días y cuánta agua le falta, "
        f"para que riegue solo cuándo y dónde lo necesita?** La hipótesis es que el balance hídrico FAO-56, alimentado con "
        f"índices de vegetación y con el pronóstico del clima, y corregido con lo aprendido de las mediciones de humedad de "
        f"Nataima, clasifica la humedad del suelo en los tres estados del semáforo mejor que el azar y detecta a tiempo la "
        f"humedad baja.")

# ---- 3.2
doc.add_heading("3.2 Análisis exploratorio de datos", 2)
doc.add_heading("3.2.1 Entendimiento de los datos", 3)
parrafo(f"Los datos de campo salen del experimento de Nataima {c('martinez')}. Los datos originales del proyecto de "
        f"investigación que lo financió son privados {c('triana')}, así que se digitalizaron de las figuras del artículo, "
        f"que es de acceso abierto: lluvia, riego y humedad del suelo por día y por técnica. La única medición directa del "
        f"suelo es la **humedad volumétrica medida con TDR** a 20 cm, y por eso es la variable objetivo. A su alrededor "
        f"están los factores que la mueven: el agua que entra (lluvia y riego) y el clima que la saca (la "
        f"evapotranspiración). La Tabla 8 resume las fuentes.")
tabla("Fuentes de datos del análisis.",
      ["Fuente", "Variables", "Resolución", "Uso en SIRA"],
      [[f"Experimento de Nataima {c('martinez')} (digitalizado)", "Humedad TDR (%), riego (mm), lluvia (mm)",
        "Por franja y día; 198 mediciones TDR", "Variable objetivo y riego"],
       [f"CHIRPS {c('funk')}", "Lluvia diaria", "0,05° (≈ 5,5 km), diaria", "Lluvia observada"],
       [f"NASA POWER {c('power')}", "Temperatura, humedad, radiación y viento", "0,5°, diaria", "ETo FAO-56"],
       [f"Sentinel-2 {c('drusch')} y Landsat, armonizados (HLS) {c('claverie')}", "Reflectancia → NDVI, NDMI, NDWI",
        "10–30 m, cada ≈ 5 días", "Índices de vegetación y agua"]],
      [1.9, 1.6, 1.5, 1.2], alinear_num=False)
parrafo(f"El experimento se hizo en **tres franjas** contiguas, una por técnica, durante **tres temporadas** (GS1, GS2 y "
        f"GS3). Para que ninguna técnica quedara ligada a un suelo, cada temporada las técnicas rotaron a la franja vecina, "
        f"de modo que todas pasaron por todas las franjas {c('martinez')} (Tabla 9). En total quedan 9 series diarias "
        f"(3 franjas × 3 temporadas) y 198 mediciones de humedad: 17 en estado bajo, 42 en medio y 139 en suficiente.")
tabla("Diseño del experimento: técnica por franja, fechas, lluvia y mediciones TDR de cada temporada.",
      ["Temporada", "Fechas (siembra → cosecha)", "Franja oeste", "Franja centro", "Franja este", "Lluvia (mm)", "TDR (n)"],
      [["GS1", "27 may – 1 oct 2021", "MIRI", "AWD+MIRI", "CD", "522", "80"],
       ["GS2", "4 dic 2021 – 11 abr 2022", "AWD+MIRI", "CD", "MIRI", "557", "48"],
       ["GS3", "9 jul – 13 nov 2022", "CD", "MIRI", "AWD+MIRI", "477", "70"]],
      [0.9, 1.55, 0.85, 0.85, 0.85, 0.6, 0.6],
      nota=f"Fuente: elaboración propia con datos digitalizados de {c('martinez')}; la rotación entre franjas está descrita en "
           f"{c('martinez')}.")

doc.add_heading("3.2.2 La humedad del suelo y sus factores", 3)
parrafo("La Figura 5 muestra las 9 series de humedad con la lluvia de cada temporada. El patrón es claro: CD, que riega "
        "sin límite, se mantiene siempre por encima de capacidad de campo; AWD+MIRI, que deja secar el suelo, es la única "
        "técnica que llega a humedad baja, y lo hace sobre todo en GS3; MIRI queda en medio. Los aguaceros fuertes apenas "
        "mueven la humedad medida. La Tabla 10 lo confirma con números: los 17 casos de humedad baja son todos de AWD+MIRI.")
figura("fig_eda_series_tdr.png", f"Humedad volumétrica medida con TDR por técnica y temporada (eje izquierdo; fondo con "
       f"los estados del semáforo) y lluvia diaria (barras, eje derecho invertido). Elaboración propia con datos "
       f"digitalizados de {c('martinez')}.")
tabla("Estadísticas de la humedad TDR (%) por temporada y técnica.",
      ["Temporada", "Técnica", "n", "Media", "Desv.", "Mín.", "Máx.", "% baja"],
      [["GS1", "CD", "35", "37,1", "3,5", "31,7", "43,8", "0"], ["GS1", "MIRI", "12", "32,2", "3,5", "27,6", "38,8", "0"],
       ["GS1", "AWD+MIRI", "33", "31,0", "3,6", "24,1", "38,1", "15"],
       ["GS2", "CD", "18", "34,6", "2,2", "30,6", "38,6", "0"], ["GS2", "MIRI", "12", "31,7", "2,1", "28,0", "34,7", "0"],
       ["GS2", "AWD+MIRI", "18", "29,9", "1,9", "26,0", "33,1", "11"],
       ["GS3", "CD", "26", "34,5", "2,6", "31,0", "40,8", "0"], ["GS3", "MIRI", "25", "29,9", "1,6", "27,2", "32,8", "0"],
       ["GS3", "AWD+MIRI", "19", "27,5", "1,9", "25,3", "32,1", "53"],
       ["Total", "", "198", "32,4", "4,0", "24,1", "43,8", "9"]],
      [0.85, 1.05, 0.5, 0.7, 0.6, 0.6, 0.6, 0.7], nota=f"Fuente: elaboración propia con datos de {c('martinez')}. "
                                                       f"% baja: mediciones por debajo de 27,1 %.")

doc.add_heading("3.2.3 Los índices satelitales de humedad: NDMI y NDWI", 3)
parrafo(f"Como el satélite no mide el suelo directamente, el plan inicial fue usar los índices de agua que se calculan con "
        f"las imágenes de Landsat y Sentinel-2 que NASA distribuye armonizadas (HLS) {c('claverie')}. El NDMI responde al agua "
        f"dentro de las hojas {c('gao')} y el NDWI al agua libre en superficie {c('mcfeeters')}; parecían la medida natural "
        f"para un cultivo inundado. La Tabla 11 da sus fórmulas.")
tabla("Índices espectrales usados y bandas de Sentinel-2.",
      ["Índice", "Fórmula", "Bandas Sentinel-2", "Qué mide", "Fuente"],
      [["NDVI", "(NIR − Rojo) / (NIR + Rojo)", "B8, B4", "Verdor y vigor de la planta", c("nasaeo")],
       ["NDMI", "(NIR − SWIR1) / (NIR + SWIR1)", "B8, B11", "Agua en las hojas", c("gao")],
       ["NDWI", "(Verde − NIR) / (Verde + NIR)", "B3, B8", "Agua libre en superficie", c("mcfeeters")]],
      [0.6, 2.0, 1.1, 1.6, 0.7], alinear_num=False)
parrafo("El análisis de correlación los descarta como medida directa (Figura 6): ni el NDMI ni el NDWI siguen a la humedad "
        "TDR (ρ = 0,00), y tampoco el NDVI (ρ = −0,03). AWD+MIRI tiene incluso el NDMI medio más alto (0,32) siendo la "
        "técnica con el suelo más seco (29,8 %). La razón es física: el satélite ve el dosel, no el suelo a 20 cm, y la "
        "planta de arroz puede tener hojas bien hidratadas aunque el suelo se esté secando. Lo que sí sigue a la humedad es "
        "el agua que entra: el riego de los últimos 5 días (ρ = 0,41) y, sobre todo, el balance acumulado S (ρ = 0,53). El "
        "clima de pocos días, por sí solo, explica poco.")
figura("fig_eda_correlaciones.png", "Correlación de Spearman de cada variable candidata con la humedad TDR, en las 198 "
       "mediciones. Elaboración propia.", ancho=6.0)

doc.add_heading("3.2.4 El NDVI como índice multiuso", 3)
parrafo(f"Si el satélite no ve el agua del suelo, ¿qué aporta? El NDVI. La clorofila absorbe la luz roja para la "
        f"fotosíntesis y la estructura de la hoja refleja el infrarrojo cercano, así que el contraste entre ambas bandas "
        f"mide cuánta planta sana hay {c('nasaeo')}. Y como en la mayoría de climas el crecimiento de la vegetación está "
        f"limitado por el agua, el NDVI es también un indicador de sequía agrícola {c('nasaeo')}. La relación matemática "
        f"la da el modelo de eficiencia en el uso de la luz {c('monteith')}, que NASA usa para calcular la productividad "
        f"de la vegetación desde el satélite {c('running')}:")
ecuacion(mi("GPP") + mo("=") + sub("ε", "máx") + mo("·") + "<mi>f</mi><mo>(</mo>" + sub("T", "mín") + "<mo>)</mo>" + mo("·")
         + "<mi>f</mi><mo>(</mo><mi>VPD</mi><mo>)</mo>" + mo("·") + mi("fAPAR") + mo("·") + mi("PAR"))
parrafo(f"La producción de la planta (GPP) es la radiación fotosintética que llega (PAR) por la fracción que absorbe el "
        f"dosel (fAPAR) y por una eficiencia que el frío y el aire seco reducen, f(Tmín) y f(VPD) {c('running')}. La "
        f"fracción absorbida se estima con la reflectancia roja e infrarroja, las mismas bandas del NDVI {c('nasaeo')}, "
        f"{c('running')}. Así, la radiación, la temperatura y el agua de las semanas anteriores quedan acumuladas en la "
        f"cantidad de hoja verde que el NDVI mide: el NDVI resume el clima indirectamente. Por eso en Nataima el NDVI "
        f"apenas se correlaciona con el clima de pocos días (ρ = 0,20 con la lluvia de 10 días) y en cambio dibuja la "
        f"curva de desarrollo del cultivo (Figura 7): sube hasta cerca de 0,85 a los 70–75 días y cae en la maduración.")
figura("fig_eda_ndvi_kc.png", "NDVI de Sentinel-2 por franja y temporada en los días con medición TDR, y su equivalencia "
       "con el coeficiente de cultivo Kc (eje derecho). Elaboración propia.")
parrafo(f"Esa misma cantidad de hoja es la que fija cuánta agua consume el cultivo: la guía FAO-56 ajusta el coeficiente "
        f"de cultivo según la fracción del suelo cubierta por la vegetación {c('fao56')}. Por eso el NDVI entra a SIRA por el "
        f"Kc, con la relación Kc = 1,0·NDVI + 0,45 (recortada entre 0,6 y 1,3), calibrada contra el TDR en el informe "
        f"preliminar.")

doc.add_heading("3.2.5 La ecuación FAO-56 como propagador", 3)
parrafo("Con el Kc del satélite, el balance de la Ec. (3) se puede escribir solo con datos que existen en cualquier lote "
        "(Ec. 8). Cada día el déficit parte del déficit de ayer, le resta la lluvia y el riego, y le suma el consumo del "
        "cultivo: es un **propagador iterativo** (Figura 8).")
ecuacion("<msub><mi>D</mi><mrow><mi>r</mi><mo>,</mo><mi>i</mi></mrow></msub>" + mo("=")
         + "<msub><mi>D</mi><mrow><mi>r</mi><mo>,</mo><mi>i</mi><mo>−</mo><mn>1</mn></mrow></msub>" + mo("−") + sub("P", "i")
         + mo("−") + sub("I", "i") + mo("+") + sub("K", "s") + mo("·") + "<mo>(</mo><mn>1,0</mn><mo>·</mo>"
         + sub("NDVI", "i") + mo("+") + mn("0,45") + "<mo>)</mo>" + mo("·") + "<msub><mi>ET</mi><mrow><mi>o</mi><mo>,</mo><mi>i</mi></mrow></msub>")
figura("fig_eda_propagador.png", "La ecuación FAO-56 como propagador: cada día toma el déficit del día anterior y lo "
       "actualiza con la lluvia, el riego, la ETo y el Kc del satélite. Elaboración propia con base en "
       f"{c('fao56')}.", ancho=6.0)
parrafo("Todas sus entradas, salvo el riego, existen hacia adelante: la lluvia y la ETo salen de un pronóstico del clima y "
        "el NDVI de un pronóstico del satélite. Por eso la ecuación puede propagar el estado del suelo 30 días, y esa es la "
        "base del semáforo. Pero sola no basta: su déficit apenas se correlaciona con la humedad medida (ρ = −0,14 en la "
        "Figura 6), porque no conoce el suelo ni el riego real de cada lote. Esa brecha es la que debe cerrar el modelo "
        "corrector de la sección 3.3.")

# ---- 3.3
doc.add_heading("3.3 Modelos, características, entrenamiento y evaluación", 2)
parrafo("SIRA combina dos modelos aprendidos y dos ecuaciones (Figura 9). El **modelo A (predictor)** pronostica el NDVI y "
        "el NDMI de cada píxel hasta 30 días. El **modelo B (corrector)** lleva las ecuaciones de balance a la humedad que "
        "mide el TDR en Nataima. Las ecuaciones S y Dr arman las entradas de B, y Dr, la del déficit FAO-56, propaga el "
        "estado del suelo hacia adelante. Los dos modelos se eligieron con la misma regla: varias familias candidatas, "
        "selección en validación, una prueba sellada que se mide una sola vez, y empate a favor del modelo más simple "
        "cuando el IC 95 % de la diferencia incluye el 0.")
figura("fig_arquitectura.png", "Arquitectura de SIRA: entradas, modelos A y B, ecuaciones S y Dr, y semáforo. "
       "Elaboración propia.", ancho=4.6)

doc.add_heading("3.3.1 Modelo A: predictor de índices", 3)
parrafo("A se entrenó con 1 412 imágenes de Sentinel-2 y Landsat 7, 8 y 9 (noviembre de 2012 a noviembre de 2026) en una "
        "ventana de 5 × 5 km alrededor de Nataima: 250 000 píxeles de 10 m, de los cuales 143 335 son agrícolas. La "
        "limpieza descartó nubes, sombras y bruma (9,4 % de las observaciones), calibró Landsat a la escala de Sentinel-2 "
        "(R² entre 0,66 y 0,84) y detectó la siembra de cada píxel con la curva del NDVI; en las tres siembras conocidas de "
        "Nataima el error mediano fue de −7, +10 y −2 días. La Tabla 12 muestra las características.")
tabla("Características del modelo A (para NDVI y NDMI).",
      ["Grupo", "Variables", "¿Entra?"],
      [["Estado actual", "NDVI y NDMI de la última imagen despejada; sensor", "Sí"],
       ["Historia reciente", "Dos imágenes anteriores, su antigüedad y la pendiente del índice", "Sí"],
       ["Fenología", "Días desde siembra y etapa, hoy y en la fecha objetivo", "Sí"],
       ["Época del año", "Seno y coseno del día del año, hoy y en la fecha objetivo", "Sí"],
       ["Horizonte", "Días hasta la fecha objetivo (1 a 30) y sensor objetivo", "Sí"],
       ["Clima", "Lluvia (CHIRPS), ETo (NASA POWER), índice ONI de El Niño", "No"],
       ["Prithvi", "Representación del modelo fundacional de NASA–IBM", "No"]],
      [1.3, 3.9, 0.8], alinear_num=False,
      nota=f"Fuente: elaboración propia. Un grupo entra solo si el IC 95 % de su mejora excluye el 0, y se confirma con la "
           f"prueba de Diebold–Mariano {c('dm')}. Prithvi: {c('prithvi')}.")
parrafo("El clima no entra: la lluvia, la ETo y el ONI no mejoran el pronóstico de forma significativa, porque lo que "
        "decide el índice de mañana es dónde está el cultivo en su curva. Esto es coherente con la sección 3.2.4: el NDVI "
        "ya acumula el clima de las semanas anteriores. La partición fue cronológica, nunca al azar: 10 años de historia "
        "para entrenar, 3 años de validación con ventana deslizante (noviembre de 2022 a noviembre de 2025) y una prueba "
        "sellada de noviembre de 2025 a septiembre de 2026. La métrica es el skill frente a la persistencia (repetir el "
        "último valor) y a la climatología (la curva típica del arroz). La Tabla 13 compara las familias en validación.")
tabla("Modelo A: skill medio en validación de la mejor configuración de cada familia.",
      ["Familia", "Skill NDVI", "Skill NDMI", "¿Empata con el mejor?"],
      [["ARIMAX", "0,074", "0,071", "No"], ["Random Forest", "0,194", "0,204", "Sí (elegido)"],
       ["Gradient Boosting", "0,195", "0,199", "Sí"], ["XGBoost", "0,195", "0,200", "Sí"], ["GRU (red recurrente)", "0,190", "0,200", "Sí"]],
      [2.2, 1.1, 1.1, 1.6], nota=f"Fuente: elaboración propia. Por la regla de empate gana el más simple de los que "
                                 f"empatan: Random Forest {c('breiman')}, con 200 árboles y 750 píxeles de entrenamiento.")
parrafo("En la prueba sellada, A le gana a la persistencia en todos los horizontes y su ventaja crece con el plazo "
        "(Tabla 14, Figura 10): a 30 días explica el 70 % de la varianza del NDVI, frente al 35 % de la persistencia. A un "
        "día casi no hay mejora, porque el error ya es sobre todo ruido de medición entre imágenes. Como A no usa clima, "
        "cambiar el clima observado por el pronóstico GEOS-S2S no le cambia el error.")
tabla("Modelo A en la prueba sellada (noviembre de 2025 a septiembre de 2026; 167 939 pares píxel–fecha).",
      ["Índice", "Horizonte", "RMSE modelo", "RMSE persistencia", "R² modelo", "R² persistencia"],
      [["NDVI", "1 día", "0,083", "0,092", "0,90", "0,88"], ["NDVI", "7 días", "0,097", "0,122", "0,85", "0,76"],
       ["NDVI", "30 días", "0,146", "0,213", "0,70", "0,35"], ["NDVI", "Todos", "0,120", "0,166", "0,79", "0,60"],
       ["NDMI", "Todos", "0,093", "0,130", "0,77", "0,54"]],
      [0.8, 1.0, 1.1, 1.2, 1.0, 1.1], nota="Fuente: elaboración propia.")
figura("fig_modelo_a_skill.png", "Skill del modelo A por horizonte en la prueba sellada, frente a la persistencia (línea "
       "continua, con IC 95 %) y frente a la climatología (línea discontinua). Elaboración propia.", ancho=5.6)

doc.add_heading("3.3.2 Modelo B: corrector de humedad", 3)
parrafo(f"B aprende de las 198 mediciones TDR a clasificar la humedad en los tres estados del semáforo. Con tan pocos "
        f"datos y con mediciones vecinas en el tiempo, la separación se hizo por bloques de 10 días, con las tres franjas "
        f"de cada bloque siempre juntas, para que el clima compartido no se filtre entre entrenamiento y prueba "
        f"{c('roberts')}. Hubo 4 particiones de prueba selladas y, dentro de cada una, una validación interna de 5 "
        f"particiones para elegir familia e hiperparámetros por AUC-ROC macro. Cada medición se probó una sola vez, con un "
        f"modelo que nunca la vio. Se compararon cinco familias, cada una con y sin Prithvi (Tabla 15, Figura 11).")
tabla("Modelo B: familias candidatas en validación y en la prueba sellada.",
      ["Familia", "Configuración elegida", "AUC validación", "AUC prueba [IC 95 %]", "Rojos detectados"],
      [["ARIMAX", "p = 1, sin Prithvi", "0,774", "0,75 [0,68–0,81]", "12 de 17"],
       ["Random Forest", "profundidad 5, hoja ≥ 3", "0,773", "0,74 [0,67–0,80]", "7 de 17"],
       ["XGBoost", "profundidad 2, + Prithvi", "0,764", "0,73 [0,65–0,79]", "6 de 17"],
       ["Gradient Boosting", "profundidad 3, + Prithvi", "0,777", "0,71 [0,62–0,79]", "4 de 17"],
       ["Regresión logística", "C = 1", "0,717", "0,68 [0,60–0,75]", "7 de 17"]],
      [1.4, 1.65, 0.95, 1.3, 0.9], nota="Fuente: elaboración propia. Rojos detectados con la regla de color de esa evaluación (probabilidad corregida, Tabla 17). ARIMAX empata en validación con Gradient Boosting + "
                                        "Prithvi y gana por ser el más simple; en la prueba es el de mejor AUC y el que "
                                        "más casos de humedad baja detecta.")
figura("fig_modelo_b_roc.png", "Curvas ROC (promedio macro de las tres clases) de cada familia del modelo B en la "
       "prueba sellada. Elaboración propia.", ancho=3.9)
parrafo(f"Prithvi {c('prithvi')} no mejoró a B (AUC 0,74 frente a 0,75, con un IC de la diferencia que incluye el 0). "
        f"La razón es de escala: su representación es una sola para el bloque de 480 m que contiene el lote, y es la misma "
        f"para las tres franjas, así que no puede distinguir cuál se está secando. El modelo elegido es un ARIMAX(1): una "
        f"regresión de la humedad sobre siete entradas, con errores autocorrelacionados de un día al siguiente (Ec. 9).")
ecuacion(sub("h", "t") + mo("=") + mi("c") + mo("+") + "<munder><mo>∑</mo><mi>k</mi></munder>"
         + sub("β", "k") + "<msub><mi>z</mi><mrow><mi>k</mi><mo>,</mo><mi>t</mi></mrow></msub>" + mo("+") + sub("u", "t")
         + mo(",") + ESP + sub("u", "t") + mo("=") + mi("φ") + "<msub><mi>u</mi><mrow><mi>t</mi><mo>−</mo><mn>1</mn></mrow></msub>"
         + mo("+") + sub("ε", "t"))
parrafo("Aquí h es la humedad TDR y z son las entradas estandarizadas. En la aplicación no hay TDR, así que B predice con "
        "el valor esperado μ = c/(1 − φ) + Σβ·z, que da la humedad en las unidades del TDR de Nataima, con un error típico "
        "σ = 3,12 %. La Tabla 16 muestra las entradas y sus coeficientes. Como las entradas están correlacionadas entre "
        "sí, el signo de cada coeficiente no se puede leer por separado.")
tabla("Entradas y coeficientes del ARIMAX(1) de uso, ajustado con las 198 mediciones.",
      ["Entrada", "Qué es", "β (puntos de humedad por desviación)"],
      [["S₂₀", "Reservorio de agua con memoria de 20 días", "+3,21"], ["S₅", "Reservorio con memoria de 5 días", "−0,95"],
       ["Dr", "Déficit FAO-56 (k = 0,5)", "+1,13"], ["Riego 5 días", "Riego de los últimos 5 días (mm)", "+1,07"],
       ["Días desde riego", "Días desde el último riego", "+0,58"], ["ETo 5 días", "Evapotranspiración de 5 días (mm)", "0,00"],
       ["NDVI", "Índice de vegetación del día", "+0,38"]],
      [1.4, 2.9, 1.9], nota="Fuente: elaboración propia. c/(1 − φ) = 32,3 % (la humedad media), φ = 0,50, σ = 3,12 %.")

doc.add_heading("3.3.3 Del corrector al semáforo", 3)
parrafo("Quedaban dos decisiones, y las dos se tomaron con datos. La primera es cómo pasar de μ a un color. μ es una media "
        "condicional y se encoge hacia el promedio: en los días que de verdad estuvieron en humedad baja, el TDR promedió "
        "25,9 %, pero μ promedió 30,4 %. Con los umbrales del artículo aplicados a μ, el semáforo no detecta ningún caso de "
        "humedad baja. Por eso al déficit se le suma un margen igual al error típico del modelo, 1,5·σ = 4,7 mm, antes de "
        "aplicar los umbrales (Tabla 17).")
tabla("Reglas de color evaluadas en la prueba sellada (198 mediciones).",
      ["Regla", "Rojos", "Amarillos", "Verdes", "Exactitud balanceada"],
      [["Déficit de B sin margen", "0 de 17", "13 de 42", "119 de 139", "0,39"],
       ["Déficit de B + margen (elegida)", "9 de 17", "24 de 42", "70 de 139", "0,53"],
       ["Probabilidad corregida por frecuencia", "12 de 17", "11 de 42", "90 de 139", "0,54"],
       ["Verde sin margen, rojo con margen", "9 de 17", "0 de 42", "119 de 139", "0,46"]],
      [2.5, 0.8, 0.9, 0.95, 1.05],
      nota="Fuente: elaboración propia. La probabilidad corregida se descartó porque pinta rojo con déficit casi nulo; la "
           "regla mixta borra el amarillo, porque el margen mide casi lo mismo que toda la franja amarilla (4,34 mm).")
parrafo("La segunda es quién propaga hacia adelante. Al darle a B las entradas propagadas 30 días sin riego, μ bajó apenas "
        "0,3 puntos: los coeficientes del déficit y de los días desde el riego tienen el signo físico al revés (Tabla 16) y "
        "cancelan el secado. Un B reentrenado solo con S tampoco siguió los secados observados entre mediciones (ρ = −0,08, "
        "IC 95 % de −0,29 a 0,10). Además, Nataima no permite validar un secado aprendido, porque entre dos mediciones pasan "
        "unos 2 días y el ruido del TDR es de unos ±4 puntos. Por eso **B se usa solo para el día de hoy**, y desde ahí "
        "propaga la física de la Ec. (8):")
ecuacion(sub("D", "r,0") + mo("=") + mn("1,5") + mo("·") + mo("(") + mn("30") + mo("−") + sub("μ", "0") + mo(")") + mo("+")
         + mn("1,5") + mo("·") + mi("σ") + mo(",") + ESP + sub("D", "r,i") + mo("→") + txt("Ec. (8)") + mo(",") + ESP
         + mi("i") + mo("=") + mn("1") + mo(",") + mo("…") + mo(",") + mn("30"))
parrafo(f"Desde la siembra hasta hoy, las entradas de B se calculan con el clima observado y un riego supuesto. Se supuso "
        f"el riego en cascada (CD), que mantiene el suelo saturado y es el más común y el más accesible para el pequeño productor (sección 2.4). "
        f"De hoy a 30 días, Dr se propaga sin riego con la lluvia y la ETo del pronóstico GEOS-S2S de NASA {c('molod')}, "
        f"corregido con su error histórico, y con el NDVI pronosticado por A. Cada día se pinta con los límites del "
        f"artículo tal cual: 0 y 4,34 mm.")

doc.add_heading("3.4 Análisis y conclusiones", 2)
doc.add_heading("3.4.1 El semáforo en operación", 3)
parrafo("La corrida oficial toma como hoy el 19 de noviembre de 2026, el último día con lluvia observada. CHIRPS se publica "
        "con semanas de retraso, así que sus últimos 50 días se completaron con la lluvia de NASA POWER. La zona piloto, de "
        "400 ha alrededor de Nataima, tiene 67,6 ha en cultivo (6 761 píxeles de 10 m). La Tabla 18 y las Figuras 12 a 14 "
        "muestran el resultado.")
tabla("Semáforo del 19 de noviembre de 2026 por horizonte, sin riego desde hoy.",
      ["Horizonte", "Fecha", "Verde", "Amarillo", "Rojo", "Déficit mediano", "Lluvia acumulada", "ETo acumulada"],
      [["Hoy", "19 nov", "99,6 %", "0,4 %", "0 %", "−6,4 mm", "—", "—"],
       ["+7 días", "26 nov", "0 %", "0 %", "100 %", "6,7 mm", "4 mm", "25 mm"],
       ["+15 días", "4 dic", "0 %", "100 %", "0 %", "2,1 mm", "40 mm", "55 mm"],
       ["+30 días", "19 dic", "0 %", "0 %", "100 %", "6,7 mm", "79 mm", "113 mm"]],
      [0.8, 0.65, 0.65, 0.75, 0.6, 1.0, 0.95, 0.9],
      nota="Fuente: elaboración propia (reports/semaforo_c). Lluvia y ETo del pronóstico GEOS-S2S del 12 de noviembre, "
           "promedio de 5 miembros. Un déficit negativo es agua por encima de capacidad de campo.")
figura("fig_res_mapas.png", "Mapa del semáforo en la zona piloto (2 × 2 km) hoy y a 7, 15 y 30 días. Verde: suficiente; "
       "amarillo: media; rojo: baja; morado: alarma del NDMI (posible enfermedad o deficiencia nutricional); gris: sin "
       "cultivo. Elaboración propia.")
parrafo("Hoy casi todo el cultivo está en verde: las lluvias recientes dejaron el suelo por encima de capacidad de campo "
        "(déficit mediano de −6,4 mm). Pero si no se riega, el 92 % del área llega a humedad baja en 3 días, y todo el "
        "cultivo entre 2 y 5 días (Figura 14). Es lo que dice la física del suelo de Nataima: la reserva útil de 6,7 mm "
        "alcanza para menos de dos días de consumo, con Kc·ETo cerca de 4 mm por día. De ahí en adelante el color sigue la "
        "lluvia pronosticada (Figura 13): tras los aguaceros de los días 12, 14 y 25 el suelo vuelve a media o suficiente, y "
        "en los tramos secos regresa a baja. En 30 días el pronóstico trae 79 mm de lluvia frente a 113 mm de ETo.")
figura("fig_res_serie30.png", "Arriba: área en cultivo en cada estado del semáforo, día a día durante 30 días sin riego. "
       "Abajo: lluvia y ETo diarias del pronóstico GEOS-S2S, y déficit mediano (líneas punteadas: 0 y 4,34 mm). "
       "Elaboración propia.", ancho=5.9)
figura("fig_res_dias_baja.png", "Días que tarda cada píxel en llegar a humedad baja si no se riega desde hoy. "
       "Elaboración propia.", ancho=4.4)
parrafo("Hay dos lecturas importantes. La primera: el mapa es casi uniforme, porque todos los píxeles comparten el mismo "
        "clima y el mismo riego supuesto, y solo los separan el NDVI y la fecha de siembra. El detalle espacial fino "
        "exigiría conocer el riego real de cada finca. La segunda: el riego supuesto hasta hoy decide el color del día. "
        "Con la corrida del 15 de noviembre se probaron los tres calendarios de Nataima (Tabla 19); con AWD+MIRI todo el "
        "mapa sale rojo, porque en Nataima esa técnica mantuvo el suelo cerca del límite (29,8 % en promedio). Por eso se "
        "supone CD, el riego que usa la mayoría. Además, el 11,4 % del área en verde lleva hoy la alarma del NDMI: hoja más "
        "seca de lo normal con el suelo húmedo, lo que apunta a revisar en campo.")
tabla("Efecto del riego supuesto hasta hoy sobre el semáforo del día (corrida del 15 de noviembre de 2026).",
      ["Riego supuesto", "μ₀ mediana", "Verde", "Amarillo", "Rojo"],
      [["CD (cascada, elegido)", "32,5 %", "10 %", "84 %", "6 %"], ["MIRI", "29,6 %", "0 %", "11 %", "89 %"],
       ["AWD+MIRI", "28,9 %", "0 %", "0 %", "100 %"]],
      [2.0, 1.1, 0.9, 1.0, 0.9], nota="Fuente: elaboración propia. μ₀: humedad de hoy estimada por el modelo B.")

doc.add_heading("3.4.2 Conclusiones del desarrollo metodológico", 3)
parrafo("La Tabla 20 contrasta lo que se esperaba al empezar con lo que mostraron los datos.")
tabla("Supuestos iniciales frente a la evidencia.",
      ["Supuesto", "Resultado", "Evidencia"],
      [["El satélite ve la humedad del suelo", "No", "NDMI, NDWI y NDVI con ρ ≈ 0 frente al TDR (Figura 6)"],
       ["El NDVI y el NDMI se pueden pronosticar a 30 días", "Sí", "Skill +0,28 y +0,29; R² 0,70 a 30 días (Tabla 14)"],
       ["El clima mejora ese pronóstico", "No", "No pasa la selección de variables; el NDVI ya integra el clima (Tabla 12)"],
       ["El corrector clasifica la humedad mejor que el azar", "Sí", "AUC 0,75 [0,68–0,81] (Tabla 15)"],
       ["El corrector detecta a tiempo la humedad baja", "En parte", "9 de 17 con la regla coherente; 1 de cada 5 rojos es real (Tabla 17)"],
       ["El corrector propaga el secado", "No", "−0,3 puntos en 30 días sin riego; propaga la FAO-56 (3.3.3)"],
       ["Prithvi mejora los modelos", "Solo para nubes", "Rellena huecos mejor que el último valor (RMSE 0,093 frente a 0,110); en B, AUC 0,74 frente a 0,75"]],
      [2.3, 0.9, 3.0], alinear_num=False)
parrafo("La respuesta a la pregunta de negocio es un sí con condiciones. Con datos gratuitos se puede dar, por píxel y "
        "para 30 días, el estado de la humedad y el déficit en L/m², y la física del balance hace que la cuenta de días "
        "hasta la humedad baja sea la información más útil para el agricultor. Las condiciones son cinco:")
for t_ in ("el corrector conoce un solo sitio y un solo suelo, con 198 mediciones digitalizadas;",
           "la propagación de 30 días es FAO-56 con los parámetros del artículo y no se pudo validar contra TDR;",
           "el margen de error cubre solo el error de B, no la dispersión del pronóstico ni el error de A;",
           "el riego de cada finca no se conoce y se supone CD;",
           "la reserva útil de 6,7 mm limita el déficit, y con él la humedad mínima que puede mostrar el semáforo (25,5 %)."):
    p_ = doc.add_paragraph(style="List Bullet")
    _runs(p_, t_)
    p_.paragraph_format.space_after = Pt(2)
doc.add_heading("4. Tecnología: ingeniería de datos", 1)
for t in ("4.1 Fuentes de datos y su naturaleza", "4.2 Ingesta", "4.3 Almacenamiento",
          "4.4 Ambiente de procesamiento", "4.5 Despliegue y aplicaciones"):
    doc.add_heading(t, 2)
    pendiente("")
doc.add_heading("5. Visualización y comunicación de datos", 1)
pendiente("5.1 Requerimientos y análisis y 5.2 Diseño e implementación del tablero (Streamlit): ya redactados por el "
          "compañero; se pegan aquí sin cambios. Lo que sigue es la estrategia de visualización de la app.")
doc.add_heading("5.3 La app SIRA para el agricultor", 2)
doc.add_heading("5.3.1 Requerimientos", 3)
parrafo("El tablero le habla al agrónomo y a los evaluadores. La app le habla a un solo público: **el agricultor, desde el "
        "celular y en el campo**. No sabe qué es un ARIMAX ni le hace falta; quiere que le respondan, sobre su propia "
        "parcela, tres preguntas: dónde regar, cuándo y cuánto. Cada pantalla de la app responde una de ellas, con los "
        "datos del semáforo detrás (Tabla 21).")
tabla("Preguntas del agricultor, cómo las responde la app y de dónde sale el dato.",
      ["Pregunta", "Elemento de la app", "Dato que lo alimenta"],
      [["¿Dónde está mi parcela?", "Buscador de los 47 municipios del Tolima, mapa y dibujo de la parcela", "Contornos de geoBoundaries y zona piloto"],
       ["¿Dónde regar?", "Mapa satelital con una celda de color por cada píxel de 10 m", "Clase del semáforo por píxel"],
       ["¿Cuándo regar?", "Deslizador y barra de 31 días; tarjeta «Cuándo» con días y fecha", "Clase diaria y días hasta humedad baja"],
       ["¿Cuánto regar?", "Tarjeta «Cuánto» en L/m²; tarjeta «Dónde» con % y hectáreas por color", "Déficit pintado por píxel"],
       ["¿Puedo confiar en esto?", "Página «Cómo funciona» y leyenda con los umbrales", "Modelos A y B, FAO-56 y [1]"]],
      [1.5, 2.7, 1.9], alinear_num=False, nota="Fuente: elaboración propia.")

doc.add_heading("5.3.2 Diseño", 3)
parrafo("La app se recorre en **dos pasos** (Figura 15): primero el agricultor ubica su parcela, y luego ve su semáforo. "
        "Todo lo demás es contexto opcional. La Tabla 22 resume las decisiones de diseño y su razón.")
figura("fig_app_recorrido.png", "Recorrido del agricultor en la app publicada: 1) ubicar la parcela; 2) elegir el día y "
       "ver el mapa; 3) tarjetas Dónde, Cuándo y Cuánto; 4) explicación del método. Capturas propias de "
       "arroz-a-tiempo-sira.lovable.app.", ancho=6.6)
tabla("Decisiones de diseño de la app.",
      ["Decisión", "Por qué"],
      [["Celular primero, botones grandes y una sola columna", "El agricultor la usa en el campo, con una mano y con sol"],
       ["Lenguaje de acción: «Regar ya», «Regar pronto», «No regar»", "Dice qué hacer, no el nombre técnico de la clase"],
       ["Los mismos colores del tablero, con los umbrales del artículo", "Un color significa lo mismo en todas las piezas"],
       ["Celdas de 10 m sobre la imagen satelital, sin suavizar", "Es la resolución real del modelo, y el agricultor reconoce su lote"],
       ["Barra de 31 días con un rectángulo por día, más el deslizador", "Se ve de un vistazo cuándo cambia el color, sin tocar cada día"],
       ["Tarjetas que resumen la parcela: %, hectáreas, días y L/m²", "Convierten miles de píxeles en tres respuestas"],
       ["Borde morado para la alarma del NDMI, encima del color", "Agrega un aviso sin cambiar el estado del agua"],
       ["Fechas del calendario además de «+7 días»", "El agricultor planea con fechas, no con horizontes"],
       ["Página «Cómo funciona» con los tres pasos y la fuente", "Transparencia: de dónde sale cada número"]],
      [2.9, 3.3], alinear_num=False, nota="Fuente: elaboración propia.")
parrafo("La Figura 16 muestra tres de esas decisiones de cerca. La barra de 31 días es un gráfico de múltiplos pequeños "
        "comprimido en una línea: cada rectángulo es un día con su color, y el día elegido se marca con un borde. El mapa "
        "muestra cada celda de 10 m tal cual sale del modelo, con el contorno de la parcela encima. La leyenda traduce "
        "cada color a humedad y a litros por metro cuadrado.")
figura("fig_app_detalle.png", "Detalle de la app: a) día elegido y barra de los 31 días; b) celdas de 10 m de la parcela "
       "de ejemplo, con borde morado donde el NDMI marca alarma; c) leyenda con los umbrales del artículo. Capturas "
       "propias.", ancho=6.3)

parrafo("La Figura 17 muestra la misma pantalla para una parcela más grande, de 24,6 ha sobre varios lotes, en otros días "
        "de la corrida del 19 de noviembre. Hoy todo está en verde, con la alarma del NDMI en morado en algunas zonas. "
        "Hacia comienzos de diciembre el pronóstico alterna aguaceros y días secos, y el mapa mezcla rojo y amarillo: los "
        "lotes no llegan al mismo tiempo al umbral, porque difieren en su NDVI (y con él en su Kc) y en su fecha de "
        "siembra. Ahí la app cumple su propósito: muestra cuáles lotes regar primero.")
figura("fig_app_dias.png", "La app en cuatro días de la corrida del 19 de noviembre de 2026, para una parcela de 24,6 ha "
       "dibujada sobre varios lotes de la zona piloto: hoy, y a 16, 18 y 20 días sin riego. Capturas propias.", ancho=6.6)

doc.add_heading("5.3.3 Implementación", 3)
parrafo("La app está hecha en Lovable (React), con el mapa en Leaflet sobre la imagen satelital de Esri, y está publicada "
        "en arroz-a-tiempo-sira.lovable.app. No calcula nada: lee dos archivos que arma el script exportar_app.py a partir "
        "de la corrida del semáforo (Tabla 23). Así, lo que ve el agricultor es exactamente lo que produjo el modelo, y "
        "actualizar la app es solo reemplazar esos archivos en el almacenamiento de datos (sección 4).")
tabla("Datos que lee la app.",
      ["Archivo", "Contenido", "Tamaño"],
      [["zonas.json", "Tolima como zona habilitada, sus 47 municipios para el buscador, la zona piloto y el mensaje para "
                      "quien busque fuera de ella", "28 kB"],
       ["semaforo.json", "Cada píxel de 10 m en cultivo: centro, días desde siembra y días hasta humedad baja; y para cada "
                         "uno de los 31 días: color, déficit en L/m² y alarma del NDMI", "1,4 MB"]],
      [1.2, 4.2, 0.8], alinear_num=False, nota="Fuente: elaboración propia (app/datos en el repositorio).")
doc.add_heading("6. Conclusiones generales", 1)
parrafo("El arroz gasta mucha agua, Colombia lo cultiva con riego en su mayoría por costumbre y en pocos años competirá sin "
        "arancel con arroz subsidiado (sección 2). SIRA se propuso responder si, con información gratuita, se le puede "
        "decir a cada lote dónde, cuándo y cuánto regar en los próximos 30 días. La respuesta es sí, con condiciones, y "
        "deja seis conclusiones.")
for titulo, texto in (
        ("El satélite no ve el agua del suelo, pero sí la planta.",
         "Los índices de agua (NDMI y NDWI) no siguen la humedad medida en Nataima. El NDVI sí aporta: resume el clima de las "
         "semanas anteriores y fija cuánta agua consume el cultivo. Por eso entra a SIRA por la física, a través del Kc, y "
         "no como medida directa de humedad."),
        ("Lo que funcionó fue combinar física y aprendizaje, cada pieza donde está probada.",
         "El modelo A pronostica la planta (R² 0,79 en la prueba sellada y skill de +0,28 frente a la persistencia); el "
         "modelo B lleva el estado de hoy a la humedad medida (AUC 0,75); y el balance FAO-56 propaga los 30 días con el "
         "pronóstico del clima. Ninguna pieza sola habría bastado."),
        ("La disciplina de validación evitó conclusiones falsas.",
         "La prueba sellada, la separación por bloques de 10 días y la regla de empate a favor del más simple descartaron "
         "lo que parecía prometedor y no lo era: Prithvi como entrada de B, el clima como entrada de A y la propagación "
         "aprendida del secado."),
        ("La información más útil para el agricultor es la cuenta de días.",
         "Con la reserva de agua del suelo de Nataima (6,7 mm), un cultivo bien regado llega a humedad baja en unos 3 días "
         "sin riego. Saber cuándo regar vale más que un mapa a 30 días, que casi siempre termina en rojo."),
        ("El producto llega a quien decide.",
         "La app responde en dos pasos y en lenguaje de acción las tres preguntas del agricultor, y el tablero da al agrónomo "
         "el detalle por lote. Las dos piezas usan el mismo código de colores y muestran exactamente lo que produce el "
         "modelo."),
        ("El potencial de ahorro es real.",
         f"En Nataima, el riego tecnificado ahorró entre 48 % y 65 % del agua sin perder cosecha {c('martinez')}, pero se "
         f"programó por inspección visual. SIRA ofrece una forma de programarlo con datos. Es la lección de los Países Bajos "
         f"(sección 2.3) aplicada al arroz: regar con datos y no por costumbre.")):
    p_ = doc.add_paragraph(style="List Number")
    p_.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    _runs(p_, f"**{titulo}** {texto}")
    p_.paragraph_format.space_after = Pt(4)
parrafo("**Trabajo futuro.** Las limitaciones de la sección 3.4.2 marcan el camino, en orden de impacto:")
for t_ in ("un botón «Regué hoy» en la app, para conocer el riego real de cada finca, que hoy es la mayor incertidumbre;",
           "mediciones de humedad en más sitios, con TDR o sensores de bajo costo, para validar la propagación de 30 días "
           "y calibrar el suelo de cada zona;",
           "incluir en el margen de error la dispersión de los miembros de GEOS-S2S y el error del modelo A;",
           "extender la zona piloto a todo el Tolima con mapas de suelo que den la reserva de agua de cada zona;",
           "dejar la actualización diaria de datos y modelos funcionando sola, sobre la arquitectura de la sección 4."):
    p_ = doc.add_paragraph(style="List Bullet")
    _runs(p_, t_)
    p_.paragraph_format.space_after = Pt(2)

# ================================================================ REFERENCIAS
doc.add_heading("Referencias", 1)
for i, (_, texto) in enumerate(REFS, 1):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.4)
    p.paragraph_format.first_line_indent = Inches(-0.4)
    p.paragraph_format.space_after = Pt(4)
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    r = p.add_run(f"[{i}]\t{texto}")
    r.font.size = Pt(9.5)
    p.paragraph_format.tab_stops.add_tab_stop(Inches(0.4))

# pie de página con número
pie = sec.footer.paragraphs[0]
pie.alignment = WD_ALIGN_PARAGRAPH.CENTER
for tipo, txt in (("begin", None), (None, "PAGE"), ("end", None)):
    r = pie.add_run()
    if tipo:
        e = OxmlElement("w:fldChar")
        e.set(qn("w:fldCharType"), tipo)
    else:
        e = OxmlElement("w:instrText")
        e.set(qn("xml:space"), "preserve")
        e.text = txt
    r._r.append(e)
sec.different_first_page_header_footer = True

doc.save(SALIDA)
print("guardado:", SALIDA, "| tablas", CONT["tabla"], "figuras", CONT["figura"], "ecuaciones", CONT["ec"])
