import os, sys, threading, time, ctypes, json, random, psutil, winsound
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QSystemTrayIcon, QMenu, QGridLayout, QCheckBox
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread, QPointF
from PyQt6.QtGui import QFont, QIcon, QPalette, QColor, QPainter, QPen, QLinearGradient
from PyQt6.QtWidgets import QProxyStyle, QStyle, QStyleOptionButton
import pydivert
import keyboard
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Set

# Filtros de pacotes de rede atualizados para clareza e consistência - CONFIGURACIÓN ORIGINAL RESTAURADA
FILTER_TELE = "(udp.DstPort>=10011 and udp.DstPort<=10020) and udp.PayloadLength>=70"  # ORIGINAL - Como funcionaba antes
FILTER_FREEZE = "inbound and udp and (udp.SrcPort>=10011 and udp.SrcPort<=10020) and udp.PayloadLength>=20 and udp.PayloadLength<=200"  # ORIGINAL - Rango que funcionaba
FILTER_DAMAGE = "inbound and udp and (udp.SrcPort>=10011 and udp.SrcPort<=10020) and udp.PayloadLength>=40 and udp.PayloadLength<=80"  # ORIGINAL - Rango exacto de daño
FILTER_GHOST = "(udp.DstPort>=10011 and udp.DstPort<=10020) and udp.PayloadLength>=50 && udp.PayloadLength<=300"  # ORIGINAL - Como funcionaba antes

# Verifica se o programa está sendo executado como administrador
if not ctypes.windll.shell32.IsUserAnAdmin():
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, __file__, None, 1)
    sys.exit()

class AppState(Enum):
    """Enumeração dos estados possíveis da aplicação"""
    IDLE = "idle"  # Ocioso
    CAPTURING_PACKETS = "capturing"  # Capturando pacotes
    WAITING_FOR_HOTKEY = "waiting_hotkey"  # Esperando por uma hotkey
    SETTING_HOTKEY = "setting_hotkey"  # Configurando uma hotkey

@dataclass
class HotkeyConfig:
    """Configuração de tecla de atalho"""
    key: str = ""  # Tecla
    display_name: str = ""  # Nome para exibição
    is_valid: bool = False  # Se a configuração é válida

@dataclass
class AppConfig:
    """Configuração principal da aplicação"""
    hotkey: HotkeyConfig  # Tecla de atalho para teleporte
    audio_enabled: bool = True  # Se os sons estão habilitados
    ghost_hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)  # Tecla de atalho para modo fantasma
    freeze_hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)  # Tecla de atalho para congelamento

@dataclass
class NetworkStats:
    """Estatísticas de rede"""
    packets_held_tele: int = 0  # Pacotes de teleporte retidos
    packets_held_ghost: int = 0  # Pacotes de modo fantasma retidos
    packets_held_freeze: int = 0  # Pacotes de congelamento retidos
    packets_sent_tele: int = 0  # Pacotes de teleporte enviados
    packets_sent_ghost: int = 0  # Pacotes de modo fantasma enviados
    packets_sent_freeze: int = 0  # Pacotes de congelamento enviados
    total_processed: int = 0  # Total de pacotes processados
    bytes_held_tele: int = 0  # Bytes de teleporte retidos
    bytes_held_ghost: int = 0  # Bytes de modo fantasma retidos
    bytes_held_freeze: int = 0  # Bytes de congelamento retidos
    bytes_sent_tele: int = 0  # Bytes de teleporte enviados
    bytes_sent_ghost: int = 0  # Bytes de modo fantasma enviados
    bytes_sent_freeze: int = 0  # Bytes de congelamento enviados
    network_usage: float = 0.0  # Uso da rede
    ping: int = 0  # Ping
    upload_speed: float = 0.0  # Velocidade de upload
    download_speed: float = 0.0  # Velocidade de download
    cpu_percent: float = 0.0  # Percentual de uso da CPU
    damage_packets_held: int = 0  # Paquetes de daño retenidos específicamente
    damage_packets_sent: int = 0  # Paquetes de daño enviados

class StatusSignals(QObject):
    """Sinais para atualização da interface do usuário"""
    update_status = pyqtSignal(str, str)  # Atualiza o status
    update_packet_count = pyqtSignal(int, str)  # Atualiza a contagem de pacotes
    update_hotkey = pyqtSignal(str)  # Atualiza a tecla de atalho
    update_ghost_hotkey = pyqtSignal(str)  # Atualiza a tecla de atalho do modo fantasma
    update_freeze_hotkey = pyqtSignal(str)  # Atualiza a tecla de atalho de congelamento
    update_button_state = pyqtSignal(bool, str)  # Atualiza o estado do botão
    update_overlay_status = pyqtSignal(dict)  # Atualiza o status do overlay
    update_network_stats = pyqtSignal(object)  # Atualiza as estatísticas de rede

class ESPOverlay(QWidget):
    """Widget de overlay que mostra o status dos modos"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # Configura a janela sem bordas, sempre no topo e transparente
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background:transparent;")
        self.mode_statuses = {
            "Teleport": False,
            "Ghost": False,
            "Freeze": False
        }
        self.dragging = False  # Se o overlay está sendo arrastado
        self.drag_position = None  # Posição do arrasto
        self.setFixedWidth(300)
        self.setFixedHeight(26)
        self.init_ui()

    def init_ui(self):
        """Inicializa a interface do usuário do overlay"""
        screen_geometry = QApplication.primaryScreen().geometry()
        width = self.width()
        height = self.height()
        self.setGeometry(screen_geometry.width() - width - 20, 12, width, height)

    def update_statuses(self, mode_states):
        """Atualiza os status dos modos"""
        self.mode_statuses = mode_states
        temp_painter = QPainter()
        temp_painter.begin(self)
        font = QFont("Arial", 9, QFont.Weight.Bold)
        temp_painter.setFont(font)
        total_text_width = 0
        padding_between_items = 15
        status_texts = []
        for mode, status in self.mode_statuses.items():
            text = f"{mode}: {'ON' if status else 'OFF'}"
            status_texts.append(text)
            total_text_width += temp_painter.fontMetrics().horizontalAdvance(text)
        total_text_width += (len(status_texts) - 1) * padding_between_items if len(status_texts) > 1 else 0
        extra_padding = 20
        new_width = total_text_width + extra_padding
        temp_painter.end()
        self.setFixedWidth(max(110, new_width))
        self.setFixedHeight(26)
        self.update()

    def paintEvent(self, event):
        """Pinta o overlay na tela"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 6, 6)
        font = QFont("Arial", 9, QFont.Weight.Bold)
        painter.setFont(font)
        x_pos = 10
        y_pos = self.height() // 2 + painter.fontMetrics().height() // 3
        padding_between_items = 15
        for mode, status in self.mode_statuses.items():
            text = f"{mode}: {'ON' if status else 'OFF'}"
            color = QColor(50, 255, 50) if status else QColor(255, 80, 80)
            painter.setPen(color)
            painter.drawText(int(x_pos), y_pos, text)
            x_pos += painter.fontMetrics().horizontalAdvance(text) + padding_between_items

    def mousePressEvent(self, event):
        """Manipula o pressionamento do mouse"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.drag_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        """Manipula o movimento do mouse"""
        if self.dragging and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = self.mapToParent(event.position().toPoint() - self.drag_position)
            screen_geo = QApplication.primaryScreen().geometry()
            new_x = max(0, min(new_pos.x(), screen_geo.width() - self.width()))
            new_y = max(0, min(new_pos.y(), screen_geo.height() - self.height()))
            self.move(new_x, new_y)
            event.accept()

    def mouseReleaseEvent(self, event):
        """Manipula a liberação do mouse"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()

    def enterEvent(self, event):
        """Manipula a entrada do cursor no overlay"""
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def leaveEvent(self, event):
        """Manipula a saída do cursor do overlay"""
        if not self.dragging:
            self.setCursor(Qt.CursorShape.ArrowCursor)

class AudioManager:
    """Gerenciador de áudio para efeitos sonoros"""
    def __init__(self, config):
        self.config = config

    def play_beep(self, frequency=500, duration=300):
        """Toca um beep"""
        if not self.config.audio_enabled:
            return
        def beep_thread():
            try:
                winsound.Beep(frequency, duration)
            except Exception:
                pass
        threading.Thread(target=beep_thread, daemon=True).start()

    def play_toggle_on(self):
        """Toca som de ativação"""
        self.play_beep(600, 120)

    def play_toggle_off(self):
        """Toca som de desativação"""
        self.play_beep(400, 150)

class ModernHotkeyManager:
    """Gerenciador de teclas de atalho moderno"""
    ALLOWED_KEYS = {
        **{f"f{i}": f"F{i}" for i in range(1, 13) if i != 10},
        **{str(i): str(i) for i in range(0, 10)},
        **{chr(i): chr(i).upper() for i in range(ord('a'), ord('z') + 1)},
        'space': 'SPACE', 'tab': 'TAB', 'enter': 'ENTER', 'shift': 'SHIFT',
        'ctrl': 'CTRL', 'alt': 'ALT', 'insert': 'INSERT', 'delete': 'DELETE',
        'home': 'HOME', 'end': 'END', 'page up': 'PAGE UP', 'page down': 'PAGE DOWN',
        'up': '↑', 'down': '↓', 'left': '←', 'right': '→'
    }
    FORBIDDEN_KEYS = {'f10', 'esc', 'windows', 'menu'}

    def __init__(self, signals, app_config, hotkey_type: str = "teleport"):
        self.signals = signals
        self.app_config = app_config
        self.hotkey_type = hotkey_type
        self.capture_active = False  # Se está capturando uma tecla
        self.capture_timeout = None  # Timeout para captura
        self.registered_hooks: List[keyboard._EventListener] = [] 
        
        # Inicializa a configuração de hotkey com valores vazios
        if hotkey_type == "teleport":
            self.current_hotkey_config = self.app_config.hotkey
        elif hotkey_type == "ghost":
            self.current_hotkey_config = self.app_config.ghost_hotkey
        elif hotkey_type == "freeze":
            self.current_hotkey_config = self.app_config.freeze_hotkey
        
        # Garante que inicialmente is_valid é False
        self.current_hotkey_config.key = ""
        self.current_hotkey_config.display_name = ""
        self.current_hotkey_config.is_valid = False

    def is_key_allowed(self, key: str) -> bool:
        """Verifica se uma tecla é permitida"""
        key_lower = key.lower()
        return (key_lower in self.ALLOWED_KEYS and key_lower not in self.FORBIDDEN_KEYS)

    def normalize_key(self, key: str) -> str:
        """Normaliza o nome da tecla para exibição"""
        key_lower = key.lower()
        return self.ALLOWED_KEYS.get(key_lower, key.upper())

    def start_capture(self, timeout: float = 10.0) -> bool:
        """Inicia a captura de uma tecla de atalho"""
        if self.capture_active:
            return False
        self.capture_active = True
        def auto_stop():
            time.sleep(timeout)
            if self.capture_active:
                self.stop_capture()
                self.signals.update_status.emit(f"⏰ Timeout - Não recebeu hotkey {self.hotkey_type}", "warning")
                # Após timeout, atualiza o estado de exibição do botão
                if self.hotkey_type == "teleport":
                    self.signals.update_hotkey.emit("Clique para definir hotkey Teleport")
                elif self.hotkey_type == "ghost":
                    self.signals.update_ghost_hotkey.emit("Clique para definir hotkey Ghost")
                elif self.hotkey_type == "freeze":
                    self.signals.update_freeze_hotkey.emit("Clique para definir hotkey Freeze")
        self.capture_timeout = threading.Thread(target=auto_stop, daemon=True)
        self.capture_timeout.start()
        return True

    def stop_capture(self):
        """Para a captura de tecla"""
        self.capture_active = False

    def try_set_hotkey(self, key: str) -> bool:
        """Tenta definir uma tecla de atalho"""
        if not self.capture_active:
            return False
        if not self.is_key_allowed(key):
            forbidden_msg = {
                'f10': "❌ F10 reservado para sair do programa",
                'esc': "❌ ESC não permitido",
                'windows': "❌ Tecla Windows não permitida",
                'menu': "❌ Tecla Menu não permitida"
            }
            self.signals.update_status.emit(forbidden_msg.get(key.lower(), "❌ Tecla não permitida"), "error")
            return False
        self.unregister_current_hotkey() # Cancela o registro da hotkey atual
        
        self.current_hotkey_config.key = key.lower()
        self.current_hotkey_config.display_name = self.normalize_key(key)
        self.current_hotkey_config.is_valid = True
        self.register_hotkey() # Registra a nova hotkey
        self._update_ui_after_set()
        self.stop_capture()
        return True

    def _update_ui_after_set(self):
        """Atualiza a interface após definir uma hotkey"""
        if self.hotkey_type == "teleport":
            self.signals.update_hotkey.emit(f"Hotkey Teleport: {self.current_hotkey_config.display_name}")
        elif self.hotkey_type == "ghost":
            self.signals.update_ghost_hotkey.emit(f"Hotkey Ghost: {self.current_hotkey_config.display_name}")
        elif self.hotkey_type == "freeze":
            self.signals.update_freeze_hotkey.emit(f"Hotkey Freeze: {self.current_hotkey_config.display_name}")
        self.signals.update_status.emit(f"✅ Hotkey {self.hotkey_type}: {self.current_hotkey_config.display_name}", "success")

    def register_hotkey(self):
        """Registra a hotkey atual"""
        if not self.current_hotkey_config.is_valid:
            return
        try:
            # Usa on_press_key em vez de add_hotkey para melhor manipulação de teclas simultâneas
            if self.hotkey_type == "teleport":
                hook = keyboard.on_press_key(self.current_hotkey_config.key, lambda e: window.toggle_tele(), suppress=False)
            elif self.hotkey_type == "ghost":
                hook = keyboard.on_press_key(self.current_hotkey_config.key, lambda e: window.toggle_ghost(), suppress=False)
            elif self.hotkey_type == "freeze":
                hook = keyboard.on_press_key(self.current_hotkey_config.key, lambda e: window.toggle_freeze(), suppress=False)
            self.registered_hooks.append(hook) # Armazena o objeto hook
        except Exception as e:
            self.signals.update_status.emit(f"Erro ao registrar hotkey {self.hotkey_type}: {e}", "error")

    def unregister_current_hotkey(self):
        """Cancela o registro da hotkey atual"""
        for hook in self.registered_hooks.copy():
            try:
                keyboard.unhook(hook) # Remove o hook usando o objeto armazenado
                self.registered_hooks.remove(hook)
            except:
                pass
        # Também remove quaisquer hotkeys registradas com add_hotkey se foram usadas anteriormente
        try:
            keyboard.remove_hotkey(self.current_hotkey_config.key)
        except:
            pass

    def save_config(self):
        """Salva a configuração (apenas audio_enabled)"""
        try:
            config_data = {
                "audio_enabled": self.app_config.audio_enabled,
                "version": "2.5" # Atualiza a versão
            }
            with open("app_config.json", "w", encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.signals.update_status.emit(f"Erro ao salvar configuração: {e}", "error")

    def load_config(self) -> bool:
        """Carrega a configuração (apenas audio_enabled)"""
        config_file = "app_config.json"
        if not os.path.exists(config_file):
            return False
        try:
            with open(config_file, "r", encoding='utf-8') as f:
                data = json.load(f)
            self.app_config.audio_enabled = data.get("audio_enabled", True)
            return True
        except Exception as e:
            self.signals.update_status.emit(f"Erro ao carregar configuração: {e}", "error")
        return False

class GreenCheckStyle(QProxyStyle):
    """Estilo personalizado para checkboxes verdes"""
    def drawPrimitive(self, element, option, painter, widget):
        if element == QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            opt = QStyleOptionButton(option)
            rect = opt.rect
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor("#404040"), 2)
            if opt.state & QStyle.StateFlag.State_MouseOver:
                pen.setColor(QColor("#00dd77") if opt.state & QStyle.StateFlag.State_On else QColor("#606060"))
            if opt.state & QStyle.StateFlag.State_On:
                pen.setColor(QColor("#00ff88"))
            painter.setPen(pen)
            painter.setBrush(QColor("#2b2b2b"))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 3, 3)
            if opt.state & QStyle.StateFlag.State_On:
                painter.setPen(QPen(QColor("#00ff88"), 2))
                x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
                painter.drawLine(int(x + w*0.25), int(y + h*0.55), int(x + w*0.45), int(y + h*0.75))
                painter.drawLine(int(x + w*0.45), int(y + h*0.75), int(x + w*0.75), int(y + h*0.33))
            painter.restore()
        else:
            super().drawPrimitive(element, option, painter, widget)

# Variáveis globais - CONFIGURACIÓN PARA DAÑO 100% GARANTIZADO
tele_mode = False  # Modo teleporte ativo
packet_tele = []  # Pacotes de teleporte armazenados
ghost_mode = False  # Modo fantasma ativo
packet_ghost = []  # Pacotes de modo fantasma armazenados (posición + movimiento)
ghost_damage_packets = []  # Pacotes de daño acumulados en modo ghost
freeze_mode = False  # Modo congelamento ativo
packet_freeze = []  # Pacotes de congelamento armazenados
damage_packets = []  # Pacotes de daño específicos
freeze_start_time = None  # Tiempo de inicio del modo freeze
last_packet_release = None  # Último tiempo de liberación automática para daño 100%
# CONFIGURACIÓN PARA DAÑO 100% GARANTIZADO
MAX_FREEZE_PACKETS = 500   # ORIGINAL - Como funcionaba súper bien antes
MAX_DAMAGE_PACKETS = 200   # ORIGINAL - Cantidad que funcionaba perfectamente
AUTO_RELEASE_INTERVAL = 10.0  # DAÑO 100% - Liberación cada 10 segundos para garantizar efectividad
DAMAGE_BURST_SIZE = 50     # MEJORADO - Mantiene bursts pero controlados
FREEZE_BURST_SIZE = 100    # MEJORADO - Mantiene bursts pero controlados
# CONFIGURACIÓN MÁXIMA POTENCIA TELEPORT Y GHOST
MAX_TELE_PACKETS = 800     # POTENTE - Máximo de paquetes teleport
MAX_GHOST_POSITION_BUFFER = 5  # CRÍTICO - Solo mantener últimos 5 paquetes de posición
TELE_BURST_SIZE = 75       # POTENTE - Burst masivo de teleport
GHOST_BURST_SIZE = 60      # POTENTE - Burst masivo de ghost
lock = threading.Lock()  # Lock para sincronização de threads
running = True  # Se o programa está em execução
app_state = AppState.IDLE  # Estado atual da aplicação
hotkey_manager_tele = None  # Gerenciador de hotkey para teleporte
hotkey_manager_ghost = None  # Gerenciador de hotkey para modo fantasma
hotkey_manager_freeze = None  # Gerenciador de hotkey para congelamento
window = None  # Referência para a janela principal

class MainWindow(QMainWindow):
    """Janela principal da aplicação"""
    def __init__(self):
        super().__init__()
        global window
        window = self
        self.signals = StatusSignals()
        self.network_stats = NetworkStats()
        
        # Inicializa AppConfig com HotkeyConfig vazias
        self.app_config = AppConfig(hotkey=HotkeyConfig(), ghost_hotkey=HotkeyConfig(), freeze_hotkey=HotkeyConfig(), audio_enabled=True)
        self.audio_manager = AudioManager(self.app_config)
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self.update_network_stats)
        self.stats_timer.start(800)
        self.init_ui()
        self.init_globals()
        self.connect_signals()
        self.init_overlay()
        self.start_backend() # Inicia o backend após a UI estar completamente inicializada
        self.session_packets_sent_tele = 0
        self.session_bytes_sent_tele = 0
        self.session_packets_sent_ghost = 0
        self.session_bytes_sent_ghost = 0
        self.session_packets_sent_freeze = 0
        self.session_bytes_sent_freeze = 0
        self.last_net = psutil.net_io_counters()
        self.last_time = time.time()
        self.last_recv = self.last_net.bytes_recv
        self.last_sent = self.last_net.bytes_sent

    def init_ui(self):
        """Inicializa a interface do usuário"""
        self.setWindowTitle("⚡ ja fat")
        self.setFixedSize(520, 480)
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2b2b2b, stop:1 #1a1a1a);
                color: white;
                border: 1px solid #404040;
            }
            QLabel {
                color: white;
                background: transparent;
            }
            QPushButton {
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: bold;
                font-size: 10px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4c4c4c, stop:1 #3c3c3c);
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5c5c5c, stop:1 #4c4c4c);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3c3c3c, stop:1 #2c2c2c);
            }
            QCheckBox {
                color: white;
                background: transparent;
                font-size: 9px;
                spacing: 8px;
            }
        """)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(6)
        title_label = QLabel("⚡ ja fat vip pro")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #00ff88; margin-bottom: 2px;")
        main_layout.addWidget(title_label)
        
        # Adiciona um separador
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setStyleSheet("color: #404040;")
        main_layout.addWidget(separator)

        # Grupo de Hotkeys
        hotkey_group_frame = QFrame()
        hotkey_group_frame.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid #404040;
                border-radius: 6px;
                padding: 8px;
            }
            QLabel {
                color: #00ff88;
                font-weight: bold;
                margin-bottom: 4px;
            }
        """)
        hotkey_layout = QVBoxLayout(hotkey_group_frame)
        hotkey_layout.setContentsMargins(6, 6, 6, 6)
        hotkey_layout.setSpacing(6)

        # Inicializa os botões de hotkey
        self.hotkey_tele_btn = QPushButton("Clique para definir hotkey Teleport")
        self.hotkey_tele_btn.clicked.connect(lambda: self.start_hotkey_capture("teleport"))
        
        self.hotkey_ghost_btn = QPushButton("Clique para definir hotkey Ghost")
        self.hotkey_ghost_btn.clicked.connect(lambda: self.start_hotkey_capture("ghost"))
        
        self.hotkey_freeze_btn = QPushButton("Clique para definir hotkey Freeze")
        self.hotkey_freeze_btn.clicked.connect(lambda: self.start_hotkey_capture("freeze"))
        
        hotkey_label = QLabel("Configuração de Hotkeys")
        hotkey_layout.addWidget(hotkey_label)
        hotkey_layout.addWidget(self.hotkey_tele_btn)
        hotkey_layout.addWidget(self.hotkey_ghost_btn)
        hotkey_layout.addWidget(self.hotkey_freeze_btn)
        main_layout.addWidget(hotkey_group_frame)
        
        # Frame de áudio
        audio_frame = QFrame()
        audio_frame.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid #404040;
                border-radius: 6px;
                padding: 8px;
                margin-top: 6px;
            }
        """)
        audio_layout = QHBoxLayout(audio_frame)
        audio_layout.setContentsMargins(6, 0, 6, 0)

        # Checkbox de áudio
        self.audio_checkbox = QCheckBox("🔊 Ativar/desativar beep")
        self.audio_checkbox.stateChanged.connect(self.on_audio_checkbox_changed)

        audio_layout.addWidget(self.audio_checkbox)
        audio_layout.addStretch(1)
        main_layout.addWidget(audio_frame)

        # Frame de estatísticas
        stats_frame = QFrame()
        stats_frame.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid #404040;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        stats_layout = QGridLayout(stats_frame)
        stats_layout.setSpacing(3)
        stats_layout.setContentsMargins(6, 4, 6, 4)
        
        # Labels para estatísticas
        self.packet_held_tele_label = QLabel("Tele Retidos: 0")
        self.packet_held_tele_label.setFont(QFont("Consolas", 8))
        self.packet_held_tele_label.setStyleSheet("color: #ff6b6b;")
        self.packet_sent_tele_label = QLabel("Tele Enviados: 0")
        self.packet_sent_tele_label.setFont(QFont("Consolas", 8))
        self.packet_sent_tele_label.setStyleSheet("color: #51cf66;")
        self.packet_held_ghost_label = QLabel("Ghost Retidos: 0")
        self.packet_held_ghost_label.setFont(QFont("Consolas", 8))
        self.packet_held_ghost_label.setStyleSheet("color: #ffd43b;")
        self.packet_sent_ghost_label = QLabel("Ghost Enviados: 0")
        self.packet_sent_ghost_label.setFont(QFont("Consolas", 8))
        self.packet_sent_ghost_label.setStyleSheet("color: #7cffd9;")
        self.packet_held_freeze_label = QLabel("Freeze Retidos: 0")
        self.packet_held_freeze_label.setFont(QFont("Consolas", 8))
        self.packet_held_freeze_label.setStyleSheet("color: #9370db;")
        self.packet_sent_freeze_label = QLabel("Freeze Enviados: 0")
        self.packet_sent_freeze_label.setFont(QFont("Consolas", 8))
        self.packet_sent_freeze_label.setStyleSheet("color: #ffc0cb;")
        self.total_processed_label = QLabel("Total: 0")
        self.total_processed_label.setFont(QFont("Consolas", 8))
        self.total_processed_label.setStyleSheet("color: #74c0fc;")
        self.bytes_held_tele_label = QLabel("B.Tele Retidos: 0")
        self.bytes_held_tele_label.setFont(QFont("Consolas", 8))
        self.bytes_held_tele_label.setStyleSheet("color: #ff6b6b;")
        self.bytes_sent_tele_label = QLabel("B.Tele Enviados: 0")
        self.bytes_sent_tele_label.setFont(QFont("Consolas", 8))
        self.bytes_sent_tele_label.setStyleSheet("color: #51cf66;")
        self.bytes_held_ghost_label = QLabel("B.Ghost Retidos: 0")
        self.bytes_held_ghost_label.setFont(QFont("Consolas", 8))
        self.bytes_held_ghost_label.setStyleSheet("color: #ffd43b;")
        self.bytes_sent_ghost_label = QLabel("B.Ghost Enviados: 0")
        self.bytes_sent_ghost_label.setFont(QFont("Consolas", 8))
        self.bytes_sent_ghost_label.setStyleSheet("color: #7cffd9;")
        self.bytes_held_freeze_label = QLabel("B.Freeze Retidos: 0")
        self.bytes_held_freeze_label.setFont(QFont("Consolas", 8))
        self.bytes_held_freeze_label.setStyleSheet("color: #9370db;")
        self.bytes_sent_freeze_label = QLabel("B.Freeze Enviados: 0")
        self.bytes_sent_freeze_label.setFont(QFont("Consolas", 8))
        self.bytes_sent_freeze_label.setStyleSheet("color: #ffc0cb;")
        self.damage_packets_label = QLabel("Daño Freeze: 0")
        self.damage_packets_label.setFont(QFont("Consolas", 8))
        self.damage_packets_label.setStyleSheet("color: #ff4757;")
        self.ghost_damage_label = QLabel("Daño Ghost: 0")
        self.ghost_damage_label.setFont(QFont("Consolas", 8))
        self.ghost_damage_label.setStyleSheet("color: #ffa502;")
        self.network_usage_label = QLabel("Rede: 0 KB/s")
        self.network_usage_label.setFont(QFont("Consolas", 8))
        self.network_usage_label.setStyleSheet("color: #d0bfff;")
        self.speed_up_label = QLabel("Up: 0 KB/s")
        self.speed_up_label.setFont(QFont("Consolas", 8))
        self.speed_up_label.setStyleSheet("color: #7cffd9;")
        self.speed_down_label = QLabel("Down: 0 KB/s")
        self.speed_down_label.setFont(QFont("Consolas", 8))
        self.speed_down_label.setStyleSheet("color: #b197fc;")
        self.cpu_label = QLabel("CPU: 0%")
        self.cpu_label.setFont(QFont("Consolas", 8))
        self.cpu_label.setStyleSheet("color: #fab005;")
        
        # Organiza os labels na grade
        row = 0
        stats_layout.addWidget(self.packet_held_tele_label, row, 0)
        stats_layout.addWidget(self.packet_sent_tele_label, row, 1)
        stats_layout.addWidget(self.packet_held_ghost_label, row, 2)
        stats_layout.addWidget(self.packet_sent_ghost_label, row, 3)
        row += 1
        stats_layout.addWidget(self.packet_held_freeze_label, row, 0)
        stats_layout.addWidget(self.packet_sent_freeze_label, row, 1)
        stats_layout.addWidget(self.total_processed_label, row, 2, 1, 2)
        row += 1
        stats_layout.addWidget(self.bytes_held_tele_label, row, 0)
        stats_layout.addWidget(self.bytes_sent_tele_label, row, 1)
        stats_layout.addWidget(self.bytes_held_ghost_label, row, 2)
        stats_layout.addWidget(self.bytes_sent_ghost_label, row, 3)
        row += 1
        stats_layout.addWidget(self.bytes_held_freeze_label, row, 0)
        stats_layout.addWidget(self.bytes_sent_freeze_label, row, 1)
        stats_layout.addWidget(self.damage_packets_label, row, 2)
        stats_layout.addWidget(self.ghost_damage_label, row, 3)
        row += 1
        stats_layout.addWidget(self.network_usage_label, row, 0)
        stats_layout.addWidget(self.speed_up_label, row, 1)
        stats_layout.addWidget(self.speed_down_label, row, 2)
        stats_layout.addWidget(self.cpu_label, row, 3)
        main_layout.addWidget(stats_frame)
        self.status_label = QLabel("Status: Pronto")
        self.status_label.setFont(QFont("Segoe UI", 8))
        self.status_label.setStyleSheet("color: #adb5bd; padding: 2px;")
        main_layout.addWidget(self.status_label)

    def init_globals(self):
        """Inicializa variáveis globais - PARA DAÑO 100% GARANTIZADO"""
        global tele_mode, packet_tele, ghost_mode, packet_ghost, ghost_damage_packets, freeze_mode, packet_freeze, damage_packets, freeze_start_time, last_packet_release, lock, running, app_state, hotkey_manager_tele, hotkey_manager_ghost, hotkey_manager_freeze
        tele_mode = False
        packet_tele = []
        ghost_mode = False
        packet_ghost = []
        ghost_damage_packets = []  # Paquetes de daño en modo ghost
        freeze_mode = False
        packet_freeze = []
        damage_packets = []
        freeze_start_time = None
        last_packet_release = None  # RESTAURADA para daño 100%
        lock = threading.Lock()
        running = True
        app_state = AppState.IDLE
        # Inicializa os gerenciadores de hotkey sem hotkeys padrão
        hotkey_manager_tele = ModernHotkeyManager(self.signals, self.app_config, "teleport")
        hotkey_manager_ghost = ModernHotkeyManager(self.signals, self.app_config, "ghost")
        hotkey_manager_freeze = ModernHotkeyManager(self.signals, self.app_config, "freeze")

    def init_overlay(self):
        """Inicializa o overlay"""
        self.overlay = ESPOverlay()
        self.overlay.show()

    def connect_signals(self):
        """Conecta os sinais aos slots correspondentes"""
        self.signals.update_status.connect(self.update_status)
        self.signals.update_packet_count.connect(self.update_packet_count)
        self.signals.update_hotkey.connect(self.update_hotkey_display)
        self.signals.update_ghost_hotkey.connect(self.update_ghost_hotkey_display)
        self.signals.update_freeze_hotkey.connect(self.update_freeze_hotkey_display)
        self.signals.update_button_state.connect(self.update_button_state)
        self.signals.update_overlay_status.connect(self.update_overlay_status)
        self.signals.update_network_stats.connect(self.update_network_stats_display)

    def start_backend(self):
        """Inicia o backend da aplicação"""
        keyboard.hook(self.handle_global_key)
        # Configura F10 para sair forçadamente
        keyboard.add_hotkey('f10', self.force_exit, suppress=True)
        
        # Carrega a configuração de áudio
        self.app_config.audio_enabled = True # Padrão True se não houver arquivo de configuração
        if hotkey_manager_tele.load_config():
            self.audio_checkbox.setChecked(self.app_config.audio_enabled)
        else:
            self.audio_checkbox.setChecked(True) # Padrão ativado se não carregar configuração

        # Atualiza a exibição inicial dos botões de hotkey
        self.update_hotkey_display("Clique para definir hotkey Teleport")
        self.update_ghost_hotkey_display("Clique para definir hotkey Ghost")
        self.update_freeze_hotkey_display("Clique para definir hotkey Freeze")

        # Inicia a thread de captura de pacotes
        threading.Thread(target=self.divert_packets, daemon=True).start()

    def on_audio_checkbox_changed(self, state):
        """Manipula mudanças no checkbox de áudio"""
        self.app_config.audio_enabled = state == Qt.CheckState.Checked.value
        hotkey_manager_tele.save_config() # Salva o estado do áudio

    def update_status(self, message, status_type):
        """Atualiza o status na interface"""
        self.status_label.setText(f"Status: {message}")

    def update_packet_count(self, count, packet_type):
        """Atualiza a contagem de pacotes"""
        if packet_type == "tele":
            self.network_stats.packets_held_tele = count
            with lock:
                self.network_stats.bytes_held_tele = sum(len(pkt.raw) for pkt in packet_tele)
        elif packet_type == "ghost":
            self.network_stats.packets_held_ghost = count
            with lock:
                # 1 paquete de posición (último) + daño acumulado
                self.network_stats.bytes_held_ghost = sum(len(pkt.raw) for pkt in packet_ghost) + sum(len(pkt.raw) for pkt in ghost_damage_packets)
        elif packet_type == "freeze":
            self.network_stats.packets_held_freeze = count
            with lock:
                self.network_stats.bytes_held_freeze = sum(len(pkt.raw) for pkt in packet_freeze) + sum(len(pkt.raw) for pkt in damage_packets)
        self.signals.update_network_stats.emit(self.network_stats)

    def auto_release_freeze_packets(self):
        """Libera automáticamente paquetes freeze para DAÑO 100% GARANTIZADO"""
        global packet_freeze, damage_packets
        try:
            if not packet_freeze and not damage_packets:
                return
                
            # SOLO liberar algunos paquetes para mantener efectividad
            # Mantener los más recientes para el burst final
            packets_to_release = min(len(packet_freeze), MAX_FREEZE_PACKETS // 4)  # Solo 25%
            damage_to_release = min(len(damage_packets), MAX_DAMAGE_PACKETS // 4)  # Solo 25%
            
            if packets_to_release == 0 and damage_to_release == 0:
                return
                
            to_send = packet_freeze[:packets_to_release]
            damage_to_send = damage_packets[:damage_to_release]
            
            # Remover solo los que vamos a enviar
            packet_freeze = packet_freeze[packets_to_release:]
            damage_packets = damage_packets[damage_to_release:]
            
            def send_auto_release(packets, damage_pkts):
                """Envía paquetes de liberación automática para daño 100%"""
                try:
                    sent_count = 0
                    damage_count = 0
                    
                    with pydivert.WinDivert(FILTER_FREEZE, layer=pydivert.Layer.NETWORK) as sender:
                        # Enviar paquetes de daño primero - PRIORIDAD MÁXIMA
                        for pkt in damage_pkts:
                            try:
                                pkt_rebuilt = pydivert.Packet(pkt.raw, pkt.interface, pkt.direction)
                                sender.send(pkt_rebuilt)
                                damage_count += 1
                            except Exception as e:
                                pass
                        
                        # Enviar algunos paquetes normales
                        for pkt in packets:
                            try:
                                pkt_rebuilt = pydivert.Packet(pkt.raw, pkt.interface, pkt.direction)
                                sender.send(pkt_rebuilt)
                                sent_count += 1
                            except Exception as e:
                                pass
                    
                    total_released = sent_count + damage_count
                    if total_released > 0:
                        self.signals.update_status.emit(f"🔄 Auto-liberación DAÑO 100%: {damage_count} daño + {sent_count} normales", "info")
                        
                except Exception as e:
                    pass
            
            if to_send or damage_to_send:
                threading.Thread(target=lambda: send_auto_release(to_send, damage_to_send), daemon=True).start()
                
        except Exception as e:
            pass

    # FUNCIÓN auto_release_freeze_packets ELIMINADA COMPLETAMENTE

    def update_hotkey_display(self, hotkey_name):
        """Atualiza a exibição da hotkey de teleporte"""
        self.hotkey_tele_btn.setText(hotkey_name)

    def update_ghost_hotkey_display(self, hotkey_name):
        """Atualiza a exibição da hotkey de modo fantasma"""
        self.hotkey_ghost_btn.setText(hotkey_name)

    def update_freeze_hotkey_display(self, hotkey_name):
        """Atualiza a exibição da hotkey de congelamento"""
        self.hotkey_freeze_btn.setText(hotkey_name)

    def update_button_state(self, is_on, mode_name):
        """Atualiza o estado do botão"""
        current_statuses = self.overlay.mode_statuses
        current_statuses[mode_name] = is_on
        self.signals.update_overlay_status.emit(current_statuses)

    def update_overlay_status(self, status_dict):
        """Atualiza o status no overlay"""
        self.overlay.update_statuses(status_dict)

    def update_network_stats(self):
        """Atualiza as estatísticas de rede"""
        try:
            net_io = psutil.net_io_counters()
            now = time.time()
            elapsed = now - self.last_time if hasattr(self, "last_time") else 1.0
            up_speed = (net_io.bytes_sent - self.last_sent) / elapsed
            down_speed = (net_io.bytes_recv - self.last_recv) / elapsed
            up_kb = up_speed / 1024
            down_kb = down_speed / 1024
            self.last_sent = net_io.bytes_sent
            self.last_recv = net_io.bytes_recv
            self.last_time = now
            self.network_stats.upload_speed = up_kb
            self.network_stats.download_speed = down_kb
            self.network_stats.network_usage = up_kb + down_kb
            self.network_stats.cpu_percent = psutil.cpu_percent(interval=None)
            self.signals.update_network_stats.emit(self.network_stats)
        except Exception as e:
            pass

    def update_network_stats_display(self, stats):
        """Atualiza a exibição das estatísticas de rede"""
        self.packet_held_tele_label.setText(f"Tele Retidos: {stats.packets_held_tele}")
        self.packet_sent_tele_label.setText(f"Tele Enviados: {self.session_packets_sent_tele}")
        self.packet_held_ghost_label.setText(f"Ghost Retidos: {stats.packets_held_ghost}")
        self.packet_sent_ghost_label.setText(f"Ghost Enviados: {self.session_packets_sent_ghost}")
        self.packet_held_freeze_label.setText(f"Freeze Retidos: {stats.packets_held_freeze}")
        self.packet_sent_freeze_label.setText(f"Freeze Enviados: {self.session_packets_sent_freeze}")
        self.total_processed_label.setText(f"Total: {stats.total_processed}")
        self.bytes_held_tele_label.setText(f"B.Tele Retidos: {self.format_bytes(stats.bytes_held_tele)}")
        self.bytes_sent_tele_label.setText(f"B.Tele Enviados: {self.format_bytes(self.session_bytes_sent_tele)}")
        self.bytes_held_ghost_label.setText(f"B.Ghost Retidos: {self.format_bytes(stats.bytes_held_ghost)}")
        self.bytes_sent_ghost_label.setText(f"B.Ghost Enviados: {self.format_bytes(self.session_bytes_sent_ghost)}")
        self.bytes_held_freeze_label.setText(f"B.Freeze Retidos: {self.format_bytes(stats.bytes_held_freeze)}")
        self.bytes_sent_freeze_label.setText(f"B.Freeze Enviados: {self.format_bytes(self.session_bytes_sent_freeze)}")
        self.damage_packets_label.setText(f"Daño Freeze: {len(damage_packets) if 'damage_packets' in globals() else 0}")
        self.ghost_damage_label.setText(f"Daño Ghost: {len(ghost_damage_packets) if 'ghost_damage_packets' in globals() else 0}")
        self.network_usage_label.setText(f"Rede: {stats.network_usage:.2f} KB/s")
        self.speed_up_label.setText(f"Up: {stats.upload_speed:.2f} KB/s")
        self.speed_down_label.setText(f"Down: {stats.download_speed:.2f} KB/s")
        self.cpu_label.setText(f"CPU: {stats.cpu_percent:.1f}%")

    def format_bytes(self, bytes_val):
        """Formata bytes para exibição"""
        if bytes_val < 1024:
            return f"{bytes_val}B"
        elif bytes_val < 1024 * 1024:
            return f"{bytes_val/1024:.1f}K"
        else:
            return f"{bytes_val/(1024*1024):.1f}M"

    def toggle_tele(self):
        """Alterna o modo teleporte"""
        global tele_mode, app_state, packet_tele
        if not hotkey_manager_tele.current_hotkey_config.is_valid: 
            self.signals.update_status.emit("⚠️ Hotkey Teleport não configurada", "warning")
            return
        if app_state == AppState.WAITING_FOR_HOTKEY:
            return
        if tele_mode:
            with lock:
                tele_mode = False
                to_send = list(packet_tele)
                packet_tele.clear()
                app_state = AppState.IDLE
            self.signals.update_button_state.emit(False, "Teleport")
            self.audio_manager.play_toggle_off() 
            def send_burst_packets(packets):
                """Envia pacotes em rajadas - CONFIGURACIÓN MÁXIMA POTENCIA"""
                try:
                    sent_count = 0
                    bytes_sent = 0
                    with pydivert.WinDivert(FILTER_TELE, layer=pydivert.Layer.NETWORK) as sender:
                        # BURST MASIVO DE TELEPORT - Envío en ráfagas potentes
                        for i in range(0, len(packets), TELE_BURST_SIZE):
                            burst = packets[i:i+TELE_BURST_SIZE]
                            for pkt in burst:
                                try:
                                    pkt_rebuilt = pydivert.Packet(pkt.raw, pkt.interface, pkt.direction)
                                    sender.send(pkt_rebuilt)
                                    sent_count += 1
                                    bytes_sent += len(pkt.raw)
                                except Exception as e:
                                    pass
                            # Micro delay entre bursts para máxima efectividad
                            if i + TELE_BURST_SIZE < len(packets):
                                time.sleep(0.003)  # 3ms entre bursts de teleport
                    self.session_packets_sent_tele = sent_count
                    self.session_bytes_sent_tele = bytes_sent
                    self.network_stats.packets_sent_tele = sent_count
                    self.network_stats.bytes_sent_tele = bytes_sent
                    self.network_stats.total_processed += sent_count
                    self.signals.update_packet_count.emit(0, "tele")
                    self.signals.update_network_stats.emit(self.network_stats)
                    
                    # Mensaje de máxima potencia
                    if sent_count > 0:
                        self.signals.update_status.emit(f"🚀 TELEPORT POTENTE liberado: {sent_count} paquetes MASIVOS", "success")
                        
                except Exception as e:
                    self.signals.update_status.emit(f"Erro ao enviar Teleport: {e}", "error")
            if to_send:
                threading.Thread(target=lambda: send_burst_packets(to_send), daemon=True).start()
            else:
                self.session_packets_sent_tele = 0
                self.session_bytes_sent_tele = 0
                self.signals.update_packet_count.emit(0, "tele")
        else:
            with lock:
                tele_mode = True
                packet_tele.clear()
                app_state = AppState.CAPTURING_PACKETS
            self.session_packets_sent_tele = 0
            self.session_bytes_sent_tele = 0
            self.signals.update_button_state.emit(True, "Teleport")
            self.audio_manager.play_toggle_on()
            self.signals.update_status.emit("🚀 TELEPORT MÁXIMA POTENCIA ACTIVADO - Capturando TODO", "info")
            self.signals.update_network_stats.emit(self.network_stats)

    def toggle_ghost(self):
        """Alterna o modo fantasma - MECÁNICA MEJORADA: Posición quieta + Acumulación de daño"""
        global ghost_mode, app_state, packet_ghost, ghost_damage_packets
        if not hotkey_manager_ghost.current_hotkey_config.is_valid:
            self.signals.update_status.emit("⚠️ Hotkey Ghost não configurada", "warning")
            return
        if app_state == AppState.WAITING_FOR_HOTKEY:
            return
        if ghost_mode:
            # DESACTIVAR: Enviar 1 paquete de posición ACTUAL + TODO el daño
            with lock:
                ghost_mode = False
                current_pos = list(packet_ghost)  # Solo 1 paquete (el último)
                damage_pkts = list(ghost_damage_packets)  # TODO el daño
                packet_ghost.clear()
                ghost_damage_packets.clear()
                app_state = AppState.IDLE
            self.signals.update_button_state.emit(False, "Ghost")
            self.audio_manager.play_toggle_off()
            def send_ghost_final(pos_pkt, dmg_packets):
                """Envía 1 paquete de posición ACTUAL + TODO el daño acumulado"""
                try:
                    pos_count = 0
                    damage_count = 0
                    bytes_sent = 0

                    with pydivert.WinDivert(FILTER_GHOST, layer=pydivert.Layer.NETWORK) as sender:
                        # PASO 1: Enviar el ÚLTIMO paquete de posición (posición actual)
                        if pos_pkt:
                            for pkt in pos_pkt:
                                try:
                                    pkt_rebuilt = pydivert.Packet(pkt.raw, pkt.interface, pkt.direction)
                                    sender.send(pkt_rebuilt)
                                    pos_count += 1
                                    bytes_sent += len(pkt.raw)
                                except Exception as e:
                                    pass
                            # Delay crítico para que posición se registre PRIMERO
                            time.sleep(0.020)  # 20ms

                        # PASO 2: Enviar TODO el DAÑO ACUMULADO
                        if dmg_packets:
                            for i in range(0, len(dmg_packets), GHOST_BURST_SIZE):
                                burst = dmg_packets[i:i+GHOST_BURST_SIZE]
                                for pkt in burst:
                                    try:
                                        pkt_rebuilt = pydivert.Packet(pkt.raw, pkt.interface, pkt.direction)
                                        sender.send(pkt_rebuilt)
                                        damage_count += 1
                                        bytes_sent += len(pkt.raw)
                                    except Exception as e:
                                        pass
                                # Micro delay entre bursts de daño
                                if i + GHOST_BURST_SIZE < len(dmg_packets):
                                    time.sleep(0.001)

                    total_sent = pos_count + damage_count
                    self.session_packets_sent_ghost = total_sent
                    self.session_bytes_sent_ghost = bytes_sent
                    self.network_stats.packets_sent_ghost = total_sent
                    self.network_stats.bytes_sent_ghost = bytes_sent
                    self.network_stats.total_processed += total_sent
                    self.signals.update_packet_count.emit(0, "ghost")
                    self.signals.update_network_stats.emit(self.network_stats)

                    # Mensaje de éxito
                    if damage_count > 0:
                        self.signals.update_status.emit(f"👻 GHOST: Posición actualizada + {damage_count} DAÑO!", "success")
                    else:
                        self.signals.update_status.emit(f"👻 GHOST: Posición actualizada", "success")

                except Exception as e:
                    self.signals.update_status.emit(f"Erro ao enviar Ghost: {e}", "error")

            if current_pos or damage_pkts:
                threading.Thread(target=lambda: send_ghost_final(current_pos, damage_pkts), daemon=True).start()
            else:
                self.session_packets_sent_ghost = 0
                self.session_bytes_sent_ghost = 0
                self.signals.update_packet_count.emit(0, "ghost")
                self.signals.update_status.emit(f"👻 GHOST desactivado", "info")
        else:
            # ACTIVAR: Retener solo última posición + acumular daño
            with lock:
                ghost_mode = True
                packet_ghost.clear()
                ghost_damage_packets.clear()
                app_state = AppState.CAPTURING_PACKETS
            self.session_packets_sent_ghost = 0
            self.session_bytes_sent_ghost = 0
            self.signals.update_button_state.emit(True, "Ghost")
            self.audio_manager.play_toggle_on()
            self.signals.update_status.emit("👻 GHOST: Posición congelada - Acumulando DAÑO", "info")
            self.signals.update_network_stats.emit(self.network_stats)

    def toggle_freeze(self):
        """Alterna o modo congelamento"""
        global freeze_mode, app_state, packet_freeze, damage_packets, freeze_start_time, last_packet_release
        if not hotkey_manager_freeze.current_hotkey_config.is_valid: 
            self.signals.update_status.emit("⚠️ Hotkey Freeze não configurada", "warning")
            return
        if app_state == AppState.WAITING_FOR_HOTKEY:
            return
        if freeze_mode:
            with lock:
                freeze_mode = False
                to_send = list(packet_freeze)
                damage_to_send = list(damage_packets)
                packet_freeze.clear()
                damage_packets.clear()
                app_state = AppState.IDLE
                freeze_duration = time.time() - freeze_start_time if freeze_start_time else 0
                freeze_start_time = None
                last_packet_release = None  # RESTAURADA para daño 100%
            self.signals.update_button_state.emit(False, "Freeze")
            self.audio_manager.play_toggle_off()
            def send_bursts_freeze(packets, damage_pkts, duration):
                """Envia pacotes de congelamento con lógica mejorada para daño"""
                try:
                    sent_count = 0
                    bytes_sent = 0
                    damage_count = 0
                    
                    with pydivert.WinDivert(FILTER_FREEZE, layer=pydivert.Layer.NETWORK) as sender:
                        # CONFIGURACIÓN ORIGINAL MEJORADA - Como funcionaba antes pero con bursts
                        damage_sent = 0
                        
                        # FASE 1: BURST DE DAÑO - Envío como antes pero en bursts
                        for i in range(0, len(damage_pkts), DAMAGE_BURST_SIZE):
                            burst = damage_pkts[i:i+DAMAGE_BURST_SIZE]
                            for pkt in burst:
                                try:
                                    # Reconstruir el paquete manteniendo todas sus propiedades
                                    pkt_rebuilt = pydivert.Packet(pkt.raw, pkt.interface, pkt.direction)
                                    sender.send(pkt_rebuilt)
                                    damage_count += 1
                                    bytes_sent += len(pkt.raw)
                                    damage_sent += 1
                                except Exception as e:
                                    pass
                            # Delay original entre bursts
                            if i + DAMAGE_BURST_SIZE < len(damage_pkts):
                                time.sleep(0.002)  # 2ms entre bursts - Como antes
                        
                        # FASE 2: BURST DE PAQUETES NORMALES
                        # Delay como funcionaba antes
                        if damage_sent > 20:
                            time.sleep(0.01)  # 10ms delay como antes
                        elif duration > 1.0:
                            time.sleep(0.05)  # Delay original
                        
                        # Enviar paquetes normales en bursts como antes
                        for i in range(0, len(packets), FREEZE_BURST_SIZE):
                            burst = packets[i:i+FREEZE_BURST_SIZE]
                            for pkt in burst:
                                try:
                                    pkt_rebuilt = pydivert.Packet(pkt.raw, pkt.interface, pkt.direction)
                                    sender.send(pkt_rebuilt)
                                    sent_count += 1
                                    bytes_sent += len(pkt.raw)
                                except Exception as e:
                                    pass
                            # Delay original entre bursts normales
                            if i + FREEZE_BURST_SIZE < len(packets):
                                time.sleep(0.001)  # 1ms entre bursts como antes
                    
                    total_sent = sent_count + damage_count
                    self.session_packets_sent_freeze = total_sent
                    self.session_bytes_sent_freeze = bytes_sent
                    self.network_stats.packets_sent_freeze = total_sent
                    self.network_stats.bytes_sent_freeze = bytes_sent
                    self.network_stats.total_processed += total_sent
                    self.signals.update_packet_count.emit(0, "freeze")
                    self.signals.update_network_stats.emit(self.network_stats)
                    
                    # Mensaje informativo sobre el daño procesado - ORIGINAL
                    if damage_count > 0:
                        self.signals.update_status.emit(f"✅ Freeze liberado: {damage_count} paquetes de daño + {sent_count} normales", "success")
                    else:
                        self.signals.update_status.emit(f"✅ Freeze liberado: {total_sent} paquetes enviados", "success")
                        
                except Exception as e:
                    self.signals.update_status.emit(f"Erro ao enviar Freeze: {e}", "error")
            
            if to_send or damage_to_send:
                threading.Thread(target=lambda: send_bursts_freeze(to_send, damage_to_send, freeze_duration), daemon=True).start()
            else:
                self.session_packets_sent_freeze = 0
                self.session_bytes_sent_freeze = 0
                self.signals.update_packet_count.emit(0, "freeze")
        else:
            with lock:
                freeze_mode = True
                packet_freeze.clear()
                damage_packets.clear()
                freeze_start_time = time.time()
                last_packet_release = time.time()  # RESTAURADA para daño 100%
                app_state = AppState.CAPTURING_PACKETS
            self.session_packets_sent_freeze = 0
            self.session_bytes_sent_freeze = 0
            self.signals.update_button_state.emit(True, "Freeze")
            self.audio_manager.play_toggle_on()
            self.signals.update_status.emit("🧊 Modo Freeze activado - Capturando paquetes de daño", "info")
            self.signals.update_network_stats.emit(self.network_stats)

    def start_hotkey_capture(self, hotkey_type: str):
        """Inicia a captura de uma hotkey"""
        global app_state
        if app_state == AppState.WAITING_FOR_HOTKEY:
            return
        app_state = AppState.WAITING_FOR_HOTKEY
        if hotkey_type == "teleport":
            self.hotkey_tele_btn.setText("Pressione a tecla Teleport...")
            if hotkey_manager_tele.start_capture(timeout=15.0):
                pass
            else:
                app_state = AppState.IDLE
        elif hotkey_type == "ghost":
            self.hotkey_ghost_btn.setText("Pressione a tecla Ghost...")
            if hotkey_manager_ghost.start_capture(timeout=15.0):
                pass
            else:
                app_state = AppState.IDLE
        elif hotkey_type == "freeze":
            self.hotkey_freeze_btn.setText("Pressione a tecla Freeze...")
            if hotkey_manager_freeze.start_capture(timeout=15.0):
                pass
            else:
                app_state = AppState.IDLE

    def handle_global_key(self, event):
        """Manipula teclas pressionadas globalmente"""
        global app_state
        if not running:
            return
        key = event.name.lower()
        if app_state == AppState.WAITING_FOR_HOTKEY and event.event_type == keyboard.KEY_DOWN:
            # Verifica qual gerenciador está capturando a tecla
            if hotkey_manager_tele.capture_active and hotkey_manager_tele.try_set_hotkey(key):
                app_state = AppState.IDLE
            elif hotkey_manager_ghost.capture_active and hotkey_manager_ghost.try_set_hotkey(key):
                app_state = AppState.IDLE
            elif hotkey_manager_freeze.capture_active and hotkey_manager_freeze.try_set_hotkey(key):
                app_state = AppState.IDLE
            return

    def divert_packets(self):
        """Captura e manipula pacotes de rede"""
        global tele_mode, packet_tele, ghost_mode, packet_ghost, ghost_damage_packets, packet_freeze, freeze_mode, damage_packets, last_packet_release, running
        try:
            combined_filter = f"({FILTER_TELE}) or ({FILTER_GHOST}) or ({FILTER_FREEZE})"
            with pydivert.WinDivert(combined_filter) as w:
                for packet in w:
                    if not running:
                        break
                    packet_handled = False
                    with lock:
                        if tele_mode and packet.direction == pydivert.Direction.OUTBOUND and packet.udp and packet.udp.payload_len >= 70:
                            packet_tele.append(packet)
                            self.network_stats.total_processed += 1
                            self.signals.update_packet_count.emit(len(packet_tele), "tele")
                            packet_handled = True
                        # GHOST MODE DEFINITIVO: Mantener SOLO último paquete de posición + acumular daño
                        if not packet_handled and ghost_mode and packet.direction == pydivert.Direction.OUTBOUND and packet.udp:
                            payload_len = packet.udp.payload_len
                            # Paquetes de DAÑO (tamaños típicos de ataques/disparos)
                            if (payload_len >= 30 and payload_len <= 50) or payload_len == 40 or payload_len == 45:
                                ghost_damage_packets.append(packet)
                                self.network_stats.total_processed += 1
                                total_ghost = len(packet_ghost) + len(ghost_damage_packets)
                                self.signals.update_packet_count.emit(total_ghost, "ghost")
                                packet_handled = True
                            # Paquetes de POSICIÓN: Mantener SOLO el ÚLTIMO (reemplazar)
                            # Esto mantiene sincronización sin enviar toda la ruta
                            elif (payload_len > 50 and payload_len < 200) or payload_len >= 60:
                                # SOBRESCRIBIR - solo mantener el más reciente
                                packet_ghost.clear()  # Borrar anteriores
                                packet_ghost.append(packet)  # Guardar SOLO este
                                self.network_stats.total_processed += 1
                                total_ghost = len(packet_ghost) + len(ghost_damage_packets)
                                self.signals.update_packet_count.emit(total_ghost, "ghost")
                                packet_handled = True
                        if not packet_handled and freeze_mode and packet.direction == pydivert.Direction.INBOUND:
                            # LÓGICA DE AUTO-LIBERACIÓN PARA DAÑO 100% GARANTIZADO
                            # Verificar si necesitamos liberar paquetes automáticamente
                            current_time = time.time()
                            should_auto_release = False
                            
                            # Liberar automáticamente si hay demasiados paquetes acumulados
                            if (len(packet_freeze) >= MAX_FREEZE_PACKETS or 
                                len(damage_packets) >= MAX_DAMAGE_PACKETS):
                                should_auto_release = True
                                
                            # Liberar automáticamente si ha pasado mucho tiempo
                            if (last_packet_release and 
                                current_time - last_packet_release >= AUTO_RELEASE_INTERVAL):
                                should_auto_release = True
                            
                            if should_auto_release:
                                # Liberar paquetes automáticamente para DAÑO 100%
                                self.auto_release_freeze_packets()
                                last_packet_release = current_time
                            
                            # Clasificar paquetes por tipo para mejor manejo del daño
                            # Verificar si es un paquete UDP del juego
                            if hasattr(packet, 'udp') and packet.udp:
                                payload_len = packet.udp.payload_len
                                src_port = packet.udp.src_port
                                
                                # Solo procesar paquetes del rango de puertos del juego
                                if 10011 <= src_port <= 10020:
                                    # RANGOS ORIGINALES RESTAURADOS - Los que funcionaban súper bien
                                    # Paquetes que probablemente contienen información de daño
                                    if 40 <= payload_len <= 80:  # ORIGINAL - Rango exacto que funcionaba
                                        damage_packets.append(packet)
                                    elif 20 <= payload_len <= 200:  # ORIGINAL - Rango que funcionaba
                                        packet_freeze.append(packet)
                                    else:
                                        # Si no coincide con los rangos, enviarlo directamente
                                        try:
                                            w.send(packet)
                                        except Exception as e:
                                            pass
                                        continue
                                else:
                                    # Paquete UDP pero no del juego, enviarlo directamente
                                    try:
                                        w.send(packet)
                                    except Exception as e:
                                        pass
                                    continue
                            else:
                                # No es UDP, enviarlo directamente
                                try:
                                    w.send(packet)
                                except Exception as e:
                                    pass
                                continue
                            
                            self.network_stats.total_processed += 1
                            total_freeze_packets = len(packet_freeze) + len(damage_packets)
                            self.signals.update_packet_count.emit(total_freeze_packets, "freeze")
                            packet_handled = True
                    if not packet_handled:
                        try:
                            w.send(packet)
                        except Exception as e:
                            pass
        except Exception as e:
            self.signals.update_status.emit(f"Erro ao inicializar WinDivert: {e}", "error")
            pass

    def force_exit(self):
        """Força a saída do programa"""
        global running
        running = False
        hotkey_manager_tele.unregister_current_hotkey()
        hotkey_manager_ghost.unregister_current_hotkey()
        hotkey_manager_freeze.unregister_current_hotkey()
        # Remove a hotkey F10 se foi registrada
        try:
            keyboard.remove_hotkey('f10')
        except:
            pass
        if hasattr(self, 'overlay'):
            self.overlay.close()
        if hasattr(self, 'stats_timer'):
            self.stats_timer.stop()
        QApplication.quit()

    def closeEvent(self, event):
        """Manipula o fechamento da janela"""
        self.force_exit()
        event.accept()

def toggle_tele():
    """Alterna o modo teleporte (função global)"""
    if window:
        window.toggle_tele()

def toggle_ghost():
    """Alterna o modo fantasma (função global)"""
    if window:
        window.toggle_ghost()

def toggle_freeze():
    """Alterna o modo congelamento (função global)"""
    if window:
        window.toggle_freeze()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle(GreenCheckStyle())
    window = MainWindow()
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(60, 60, 60))
    palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
    app.setPalette(palette)
    window.show()
    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        window.force_exit()
    except Exception as e:
        window.force_exit()