from core.validacion import CARNET_PLACEHOLDER, carnet_valido, normalizar_carnet
from PyQt6.QtCore import QRegularExpression, Qt, pyqtSignal
from PyQt6.QtGui import QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import servicio
from ui.log import log

SEDE_SAN_MIGUEL = "San Miguel"
SEDE_MORAZAN = "San Francisco Gotera (Morazán)"
SEDE_LA_UNION = "La Unión"

SEDES = [SEDE_SAN_MIGUEL, SEDE_MORAZAN, SEDE_LA_UNION]

# El servidor valida carrera y facultad contra una copia de este catálogo
# (`servidor/models/catalogo.py` en el repo del servidor): si se agrega o
# renombra una carrera acá, actualizar primero el servidor. Si no, rechazará
# el registro con 422 y descartará la carrera de las sesiones de ese kiosko.

# Departamento Académico -> carreras (Facultad Multidisciplinaria de Oriente, sede San Miguel)
DEPARTAMENTOS_SAN_MIGUEL = {
    "Departamento de Ingeniería y Arquitectura": [
        "Arquitectura",
        "Ingeniería Civil",
        "Ingeniería Industrial",
        "Ingeniería Mecánica",
        "Ingeniería Eléctrica",
        "Ingeniería en Sistemas Informáticos",
    ],
    "Departamento de Ciencias Económicas": [
        "Licenciatura en Administración de Empresas",
        "Licenciatura en Contaduría Pública",
        "Licenciatura en Economía",
        "Licenciatura en Mercadeo Internacional",
        "Licenciatura en Logística Comercial Internacional (Modalidad a Distancia)",
    ],
    "Departamento de Ciencias y Humanidades": [
        "Licenciatura en Psicología",
        "Licenciatura en Sociología",
        "Licenciatura en Letras",
        "Licenciatura en Trabajo Social",
        "Licenciatura en Lenguas Modernas Especialidad Francés e Inglés",
        "Licenciatura en Ciencias de la Educación",
        "Licenciatura en Educación Inicial y Parvularia",
        "Profesorado en Educación Inicial y Parvularia",
        "Profesorado en Educación Básica",
        "Profesorado en Ciencias Sociales",
        "Profesorado en Idioma Inglés",
    ],
    "Departamento de Ciencias Naturales y Matemática": [
        "Licenciatura en Biología",
        "Licenciatura en Ciencias Químicas",
        "Licenciatura en Física",
        "Licenciatura en Matemática",
        "Profesorado en Biología",
        "Profesorado en Física",
        "Profesorado en Matemática",
        "Profesorado en Química",
    ],
    "Departamento de Medicina": [
        "Doctorado en Medicina",
        "Licenciatura en Laboratorio Clínico",
        "Licenciatura en Fisioterapia y Terapia Ocupacional",
        "Licenciatura en Anestesiología e Inhaloterapia",
    ],
    "Departamento de Química y Farmacia": [
        "Licenciatura en Química y Farmacia",
    ],
    "Departamento de Ciencias Agronómicas": [
        "Ingeniería Agronómica",
    ],
    "Departamento de Jurisprudencia y Ciencias Sociales": [
        "Licenciatura en Ciencias Jurídicas",
    ],
}

# Sedes/extensiones sin división por departamento
CARRERAS_POR_EXTENSION = {
    SEDE_MORAZAN: [
        "Técnico en Veterinaria y Zootecnia",
        "Técnico en Agricultura Sostenible",
        "Técnico en Turismo Ecológico y Cultural",
        "Técnico en Gestión del Desarrollo Territorial",
    ],
    SEDE_LA_UNION: [
        "Técnico en Veterinaria y Zootecnia",
    ],
}

_SIN_DEPARTAMENTO = "No aplica (extensión)"
_PREFIJO_EXTENSION = "Extensión "

GENEROS = [
    ("Masculino", "M"),
    ("Femenino", "F"),
    ("LGBTIQ+", "LGBTIQ+"),
    ("Prefiero no decirlo", "N/D"),
]

SECTORES = ["Estudiante", "Administrativo", "Docente", "Visitante"]


class PantallaRegistro(QWidget):
    registro_exitoso = pyqtSignal(dict)
    actualizacion_exitosa = pyqtSignal(dict)
    acceso_no_estudiante = pyqtSignal(str)
    cancelar = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._modo_actualizacion = False
        self._construir_ui()

    def _combo_buscable(self, items, placeholder=""):
        """QComboBox editable con autocompletado: el usuario escribe y la
        lista de opciones se filtra en vivo, como un input con sugerencias."""
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if placeholder:
            combo.lineEdit().setPlaceholderText(placeholder)
        self._set_items_buscables(combo, items)
        return combo

    def _set_items_buscables(self, combo: QComboBox, items):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        combo.setCurrentIndex(-1)
        combo.blockSignals(False)
        completer = QCompleter(items, combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        combo.setCompleter(completer)

    def _construir_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(60, 30, 60, 30)

        self.lbl_titulo = QLabel("Registro de Nuevo Estudiante")
        self.lbl_titulo.setObjectName("titulo")
        self.lbl_titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.lbl_titulo)

        self.lbl_subtitulo = QLabel("Complete sus datos para registrarse.")
        self.lbl_subtitulo.setObjectName("subtitulo")
        self.lbl_subtitulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.lbl_subtitulo)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._form = form

        self.nombre = QLineEdit()
        self.nombre.setPlaceholderText("Nombre completo")
        # Mismo límite que el servidor: un nombre más largo haría que rechace
        # la sesión al sincronizarla.
        self.nombre.setMaxLength(255)
        form.addRow("Nombre:", self.nombre)

        self.carnet = QLineEdit()
        self.carnet.setPlaceholderText(f"Número de carnet ({CARNET_PLACEHOLDER})")
        self.carnet.setMaxLength(7)
        self.carnet.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[A-Za-z]{0,2}|[A-Za-z]{2}[0-9]{0,5}"))
        )
        self.carnet.textEdited.connect(self._forzar_mayusculas_carnet)
        form.addRow("Carnet:", self.carnet)

        from datetime import date as _date
        anio_actual = _date.today().year
        self._anios_validos = [str(a) for a in range(anio_actual - 15, anio_actual - 100, -1)]
        self.fecha_nac = self._combo_buscable(self._anios_validos, "Seleccione o escriba el año")
        form.addRow("Año de nacimiento:", self.fecha_nac)

        self.sector = QComboBox()
        self.sector.addItems(SECTORES)
        self.sector.currentTextChanged.connect(self._on_sector_changed)
        form.addRow("Sector:", self.sector)

        self.sede = self._combo_buscable(SEDES, "Seleccione o escriba la sede")
        self.sede.setCurrentText(SEDE_SAN_MIGUEL)
        self.sede.currentTextChanged.connect(self._actualizar_departamentos)
        form.addRow("Sede:", self.sede)

        self.departamento = self._combo_buscable(
            list(DEPARTAMENTOS_SAN_MIGUEL.keys()), "Seleccione o escriba el departamento"
        )
        self.departamento.currentTextChanged.connect(self._actualizar_carreras)
        form.addRow("Departamento:", self.departamento)

        self.carrera = self._combo_buscable([], "Escriba para buscar su carrera")
        form.addRow("Carrera:", self.carrera)

        self.genero = QComboBox()
        for etiqueta, valor in GENEROS:
            self.genero.addItem(etiqueta, valor)
        form.addRow("Género:", self.genero)

        scroll.setWidget(form_widget)
        outer.addWidget(scroll)

        self.lbl_error = QLabel("")
        self.lbl_error.setObjectName("error")
        self.lbl_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.lbl_error)

        btn_row = QHBoxLayout()
        btn_cancelar = QPushButton("Cancelar")
        btn_cancelar.setObjectName("btn-secundario")
        btn_cancelar.clicked.connect(self.cancelar)
        btn_row.addWidget(btn_cancelar)

        self.btn_guardar = QPushButton("Registrarme")
        self.btn_guardar.clicked.connect(self._guardar)
        btn_row.addWidget(self.btn_guardar)
        outer.addLayout(btn_row)

        self._actualizar_departamentos(SEDE_SAN_MIGUEL)
        self._on_sector_changed(self.sector.currentText())

    def _forzar_mayusculas_carnet(self, texto: str):
        pos = self.carnet.cursorPosition()
        self.carnet.setText(texto.upper())
        self.carnet.setCursorPosition(pos)

    def _on_sector_changed(self, sector: str):
        """Solo estudiantes se registran: el resto únicamente indica a qué
        sector pertenece y continúa sin dejar datos personales."""
        es_estudiante = sector == "Estudiante"
        for campo in (
            self.nombre, self.carnet, self.fecha_nac,
            self.sede, self.carrera, self.genero,
        ):
            campo.setEnabled(es_estudiante)
        self.departamento.setEnabled(es_estudiante and self.sede.currentText() == SEDE_SAN_MIGUEL)

        if es_estudiante:
            self.lbl_titulo.setText("Registro de Nuevo Estudiante")
            self.lbl_subtitulo.setText("Complete sus datos para registrarse.")
            self.btn_guardar.setText("Actualizar datos" if self._modo_actualizacion else "Registrarme")
        else:
            self.lbl_titulo.setText("Acceso sin registro")
            self.lbl_subtitulo.setText(f"Ingresará como {sector.lower()}, sin registrar datos personales.")
            self.btn_guardar.setText("Continuar")
        self.lbl_error.setText("")

    def _actualizar_departamentos(self, sede: str):
        if sede == SEDE_SAN_MIGUEL:
            self.departamento.setEnabled(True)
            self._set_items_buscables(self.departamento, list(DEPARTAMENTOS_SAN_MIGUEL.keys()))
        else:
            self.departamento.setEnabled(False)
            self._set_items_buscables(self.departamento, [_SIN_DEPARTAMENTO])
            self.departamento.setCurrentIndex(0)
        self._actualizar_carreras()

    def _actualizar_carreras(self, *_):
        sede = self.sede.currentText().strip()
        if sede == SEDE_SAN_MIGUEL:
            carreras = DEPARTAMENTOS_SAN_MIGUEL.get(self.departamento.currentText().strip(), [])
        else:
            carreras = CARRERAS_POR_EXTENSION.get(sede, [])
        self._set_items_buscables(self.carrera, carreras)

    def cargar_datos(self, datos: dict):
        self._modo_actualizacion = True
        self.btn_guardar.setText("Actualizar datos")
        self.nombre.setText(datos.get("nombre", ""))
        self.carnet.setText(datos.get("carnet", ""))
        self.carnet.setReadOnly(True)
        self.fecha_nac.setCurrentText(str(datos.get("fecha_nacimiento") or ""))
        self.sector.setCurrentText("Estudiante")

        facultad = datos.get("facultad", "") or ""
        if facultad.startswith(_PREFIJO_EXTENSION) and facultad[len(_PREFIJO_EXTENSION):] in CARRERAS_POR_EXTENSION:
            self.sede.setCurrentText(facultad[len(_PREFIJO_EXTENSION):])
        elif facultad in DEPARTAMENTOS_SAN_MIGUEL:
            self.sede.setCurrentText(SEDE_SAN_MIGUEL)
            self.departamento.setCurrentText(facultad)
        else:
            self.sede.setCurrentText(SEDE_SAN_MIGUEL)

        self.carrera.setCurrentText(datos.get("carrera", ""))

        idx = self.genero.findData(datos.get("sexo", ""))
        self.genero.setCurrentIndex(idx if idx >= 0 else 0)

        self.lbl_error.setText("")

    def _guardar(self):
        sector = self.sector.currentText()
        if sector != "Estudiante":
            self.acceso_no_estudiante.emit(sector)
            self._limpiar()
            return

        nombre = self.nombre.text().strip()
        carnet = normalizar_carnet(self.carnet.text())

        if not nombre or not carnet:
            self.lbl_error.setText("Nombre y carnet son obligatorios")
            return

        if not self._modo_actualizacion and not carnet_valido(carnet):
            # En modo actualización el campo es de solo lectura (carnet ya
            # existente); no tiene sentido revalidar un valor que el usuario
            # no puede editar.
            self.lbl_error.setText(f"Formato de carnet inválido. Use el formato {CARNET_PLACEHOLDER}.")
            return

        anio_nac = self.fecha_nac.currentText().strip()
        if anio_nac and anio_nac not in self._anios_validos:
            self.lbl_error.setText("Seleccione un año de nacimiento válido de la lista.")
            return

        sede = self.sede.currentText().strip()
        if sede not in SEDES:
            self.lbl_error.setText("Seleccione una sede válida de la lista.")
            return

        if sede == SEDE_SAN_MIGUEL:
            departamento = self.departamento.currentText().strip()
            if departamento not in DEPARTAMENTOS_SAN_MIGUEL:
                self.lbl_error.setText("Seleccione un departamento válido de la lista.")
                return
            carreras_validas = DEPARTAMENTOS_SAN_MIGUEL[departamento]
            facultad_valor = departamento
        else:
            carreras_validas = CARRERAS_POR_EXTENSION[sede]
            facultad_valor = f"{_PREFIJO_EXTENSION}{sede}"

        carrera = self.carrera.currentText().strip()
        if carrera not in carreras_validas:
            self.lbl_error.setText("Seleccione una carrera válida de la lista para la sede/departamento elegido.")
            return

        datos = {
            "nombre": nombre,
            "carnet": carnet,
            "fecha_nacimiento": anio_nac,
            "carrera": carrera,
            "facultad": facultad_valor,
            "sexo": self.genero.currentData(),
        }

        # El servicio comprueba si el carnet ya existe, lo envía al servidor
        # y, si no hay red o el servidor falla, lo deja pendiente en la caché
        # local para reintentarlo en el siguiente ciclo de sync.
        modo_servicio = "actualizar" if self._modo_actualizacion else "crear"
        try:
            resultado = servicio.llamar("guardar_estudiante", modo=modo_servicio, datos=datos)
        except servicio.ErrorServicio as exc:
            if exc.codigo == "carnet_duplicado":
                self.lbl_error.setText("Este carnet ya está registrado. Inicie sesión con su carnet.")
            elif exc.codigo == "sin_permiso":
                self.lbl_error.setText("Solo puede actualizar sus datos durante su propia sesión.")
            elif exc.codigo == "servidor_no_disponible":
                self.lbl_error.setText("No se pudo verificar el carnet con el servidor. Intente de nuevo en unos segundos.")
            else:
                log.error("Registro rechazado por el servicio: %s", exc)
                self.lbl_error.setText("Datos inválidos. Revise el formulario.")
            return
        except servicio.ServicioNoDisponible as exc:
            log.error("No se pudo registrar: %s", exc)
            self.lbl_error.setText("El sistema no está disponible. Intente de nuevo en unos segundos.")
            return

        datos = resultado["estudiante"]
        if resultado["estado"] == "pendiente_error":
            self.lbl_error.setText("Error al enviar al servidor — guardado localmente, se reintentará")
        elif resultado["estado"] == "pendiente_sin_conexion":
            self.lbl_error.setText("Sin internet — guardado localmente, se sincronizará después")

        modo = self._modo_actualizacion
        self._limpiar()

        if modo:
            self.actualizacion_exitosa.emit(datos)
        else:
            self.registro_exitoso.emit(datos)

    def _limpiar(self):
        self._modo_actualizacion = False
        self.carnet.setReadOnly(False)
        self.nombre.clear()
        self.carnet.clear()
        self.fecha_nac.setCurrentIndex(-1)
        self.sector.setCurrentIndex(0)
        self.sede.setCurrentText(SEDE_SAN_MIGUEL)
        self.genero.setCurrentIndex(0)
        self._on_sector_changed(self.sector.currentText())
        self.lbl_error.setText("")
