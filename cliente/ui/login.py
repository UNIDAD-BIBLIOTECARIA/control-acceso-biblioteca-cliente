from pathlib import Path

from core.validacion import CARNET_PLACEHOLDER, carnet_valido, normalizar_carnet
from PyQt6.QtCore import QRegularExpression, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from ui import servicio
from ui.log import log


class PantallaLogin(QWidget):
    login_exitoso = pyqtSignal(dict)
    ir_registro = pyqtSignal()
    solicitar_apagar = pyqtSignal()
    solicitar_reiniciar = pyqtSignal()
    solicitar_cerrar_programa = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._construir_ui()

    def _construir_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(20)
        layout.setContentsMargins(80, 60, 80, 60)

        # Logo
        logo_path = Path(__file__).parent.parent / "assets" / "logo.png"
        if logo_path.exists():
            lbl_logo = QLabel()
            pix = QPixmap(str(logo_path)).scaledToHeight(100, Qt.TransformationMode.SmoothTransformation)
            lbl_logo.setPixmap(pix)
            lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl_logo)

        lbl_titulo = QLabel("Biblioteca Universitaria")
        lbl_titulo.setObjectName("titulo")
        lbl_titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_titulo)

        lbl_sub = QLabel("Ingrese su número de carnet para continuar")
        lbl_sub.setObjectName("subtitulo")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_sub)

        layout.addSpacerItem(QSpacerItem(0, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        self.carnet_input = QLineEdit()
        self.carnet_input.setPlaceholderText(f"Número de carnet ({CARNET_PLACEHOLDER})")
        self.carnet_input.setMaxLength(7)
        self.carnet_input.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[A-Za-z]{0,2}|[A-Za-z]{2}[0-9]{0,5}"))
        )
        self.carnet_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.carnet_input.textEdited.connect(self._forzar_mayusculas)
        self.carnet_input.returnPressed.connect(self._intentar_login)
        layout.addWidget(self.carnet_input)

        self.lbl_error = QLabel("")
        self.lbl_error.setObjectName("error")
        self.lbl_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_error)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        btn_registrar = QPushButton("Registrarme")
        btn_registrar.setObjectName("btn-secundario")
        btn_registrar.clicked.connect(self.ir_registro)
        btn_row.addWidget(btn_registrar)

        btn_entrar = QPushButton("Ingresar")
        btn_entrar.clicked.connect(self._intentar_login)
        btn_row.addWidget(btn_entrar)

        layout.addLayout(btn_row)

        layout.addSpacerItem(QSpacerItem(0, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        # Controles de administrador, discretos en la esquina inferior
        # derecha. Requieren el PIN de administrador (ver
        # VentanaKiosko._verificar_pin_admin) antes de ejecutarse.
        fila_admin = QHBoxLayout()
        fila_admin.addStretch()

        btn_apagar = QPushButton("Apagar equipo")
        btn_apagar.setObjectName("btn-admin")
        btn_apagar.clicked.connect(self.solicitar_apagar)
        fila_admin.addWidget(btn_apagar)

        btn_reiniciar = QPushButton("Reiniciar equipo")
        btn_reiniciar.setObjectName("btn-admin")
        btn_reiniciar.clicked.connect(self.solicitar_reiniciar)
        fila_admin.addWidget(btn_reiniciar)

        btn_cerrar_programa = QPushButton("Cerrar programa")
        btn_cerrar_programa.setObjectName("btn-admin")
        btn_cerrar_programa.clicked.connect(self.solicitar_cerrar_programa)
        fila_admin.addWidget(btn_cerrar_programa)

        layout.addLayout(fila_admin)

    def _forzar_mayusculas(self, texto: str):
        pos = self.carnet_input.cursorPosition()
        self.carnet_input.setText(texto.upper())
        self.carnet_input.setCursorPosition(pos)

    def _intentar_login(self):
        carnet = normalizar_carnet(self.carnet_input.text())
        if not carnet:
            self.lbl_error.setText("Ingrese su carnet")
            return
        if not carnet_valido(carnet):
            self.lbl_error.setText(f"Formato de carnet inválido. Use el formato {CARNET_PLACEHOLDER}.")
            return

        self.lbl_error.setText("Buscando...")

        try:
            est = servicio.llamar("buscar_estudiante", carnet=carnet)
        except servicio.ErrorServicio as exc:
            log.error("No se pudo buscar el carnet: %s", exc)
            if exc.codigo == "servidor_no_disponible":
                self.lbl_error.setText("No se pudo consultar el servidor. Intente de nuevo en unos segundos.")
            else:
                self.lbl_error.setText("El sistema no está disponible. Intente de nuevo en unos segundos.")
            return
        except servicio.ServicioNoDisponible as exc:
            log.error("No se pudo buscar el carnet: %s", exc)
            self.lbl_error.setText("El sistema no está disponible. Intente de nuevo en unos segundos.")
            return

        if est:
            self.lbl_error.setText("")
            self.carnet_input.clear()
            self.login_exitoso.emit(est)
        else:
            self.lbl_error.setText("Carnet no encontrado. ¿Es su primera vez? Regístrese.")

    def limpiar(self):
        self.carnet_input.clear()
        self.lbl_error.setText("")
        self.carnet_input.setFocus()
