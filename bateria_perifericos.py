"""
Bateria dos Periféricos — indicador na bandeja do Windows.

Mostra um ícone por periférico (mouse VXE, teclado AULA, fone Corsair) com a
porcentagem da bateria e avisa quando cada um passa de 50%, 25% e 10%.

Protocolos portados do projeto Battery Hub (MIT, github.com/battery-hub/battery-hub),
que por sua vez se baseou em hid-kysona/hid-pulsar (Linux), HeadsetControl e
AulaF87ProBattery.

Uso:
    pythonw bateria_perifericos.py          # roda na bandeja
    python  bateria_perifericos.py --scan   # diagnóstico no terminal
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

try:
    import hid  # pacote "hidapi"
except ImportError:  # deixa os testes de lógica rodarem sem hidapi
    hid = None

APP_NAME = "BateriaPerifericos"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
STATE_PATH = os.path.join(CONFIG_DIR, "estado.json")

DEFAULT_CONFIG = {
    "limites": [50, 25, 10],      # porcentagens que geram notificação
    "intervalo_segundos": 300,    # de quanto em quanto tempo ler as baterias
    "histerese": 5,               # precisa subir limite+5% para o aviso "rearmar"
    "nomes": {},                  # ex.: {"3554:f58a": "Mouse VXE R1"}
}

KIND_LABEL = {"mouse": "Mouse", "keyboard": "Teclado", "headset": "Fone"}


# --------------------------------------------------------------------------- HID helpers

@dataclass
class Channel:
    path: bytes
    usage_page: int
    usage: int
    interface: int


@dataclass
class Device:
    vendor_id: int
    product_id: int
    name: str
    channels: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"


@dataclass
class Reading:
    capacity: int | None = None
    charging: bool | None = None
    offline: bool = False
    error: str | None = None


class DeviceOff(Exception):
    pass


class WriteError(OSError):
    pass


def list_devices() -> list[Device]:
    """Agrupa as coleções HID por VID:PID (um produto aparece como várias coleções)."""
    devices: dict[tuple[int, int], Device] = {}
    for info in hid.enumerate():
        k = (info["vendor_id"], info["product_id"])
        dev = devices.get(k)
        if dev is None:
            name = " ".join(filter(None, [info.get("manufacturer_string"), info.get("product_string")])).strip()
            dev = devices[k] = Device(k[0], k[1], name or f"{k[0]:04x}:{k[1]:04x}")
        dev.channels.append(Channel(info["path"], info.get("usage_page", 0), info.get("usage", 0),
                                    info.get("interface_number", -1)))
    return list(devices.values())


def pad(data: list[int], length: int) -> list[int]:
    return data + [0] * max(0, length - len(data))


class Handle:
    """Handle HID aberto, com leitura com timeout."""

    def __init__(self, path: bytes):
        self.path = path
        self.dev = hid.device()
        self.dev.open_path(path)

    def write(self, data: list[int]) -> None:
        if self.dev.write(data) < 0:
            raise WriteError("cannot write")

    def read(self, timeout_ms: int) -> list[int]:
        # timeout 0 no hidapi = leitura bloqueante; usa 1 ms no mínimo
        return list(self.dev.read(256, max(1, timeout_ms)) or [])

    def drain(self) -> None:
        for _ in range(64):
            if not self.read(0):
                break

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def set_output_report(path: bytes, reports: list[list[int]]) -> int:
    """Envia relatórios com HidD_SetOutputReport (transferência de controle).
    Alguns receptores (AULA/Compx na bateria) ignoram o mesmo relatório enviado com WriteFile."""
    if sys.platform != "win32":
        return 0
    k32 = ctypes.windll.kernel32
    hiddll = ctypes.windll.hid
    k32.CreateFileW.restype = ctypes.c_void_p
    h = k32.CreateFileW(path.decode() if isinstance(path, bytes) else path,
                        0xC0000000, 3, None, 3, 0, None)
    if h in (None, ctypes.c_void_p(-1).value):
        return 0
    sent = 0
    try:
        for r in reports:
            buf = (ctypes.c_ubyte * len(r))(*r)
            if hiddll.HidD_SetOutputReport(ctypes.c_void_p(h), buf, len(r)):
                sent += 1
            time.sleep(0.06)
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))
    return sent


# --------------------------------------------------------------------------- VXE (Kysona/Compx mouse)
# Pedido: relatório 0x08, sub-comando 0x04, checksum fixo 0x49. Resposta: byte 6 = %,
# byte 7 = carregando, bytes 8-9 = mV. O status (0x03) diz se o receptor enxerga o mouse.

class Kysona:
    kind = "mouse"
    BATTERY = [0x08, 0x04] + [0] * 14 + [0x49]
    STATUS = [0x08, 0x03] + [0] * 14 + [0x4a]
    VXE_VIDS = {0x3554}
    CHIP_VIDS = {0x373b, 0x25a7, 0x260d}

    @staticmethod
    def _has(dev, page, usage):
        return any(c.usage_page == page and c.usage == usage for c in dev.channels)

    @classmethod
    def matches(cls, dev: Device) -> bool:
        if Compx.matches(dev):
            return False
        if dev.vendor_id in cls.VXE_VIDS:
            return any(c.usage_page == 0xFF02 for c in dev.channels)
        return dev.vendor_id in cls.CHIP_VIDS and cls._has(dev, 0xFF02, 2) and cls._has(dev, 0xFF04, 2)

    @staticmethod
    def parse_battery(f):
        if len(f) < 10 or f[0] != 0x08 or f[1] != 0x04:
            return None
        if not (0 < f[6] <= 100):
            return None  # quadro zerado de "ainda não pronto"
        return Reading(capacity=f[6], charging=bool(f[7]))

    @staticmethod
    def parse_status(f):
        if len(f) < 17 or f[0] != 0x08 or f[1] != 0x03 or f[6] > 1:
            return None
        if ((0x55 - sum(f[:16])) & 0xFF) != f[16]:
            return None
        return f[6] == 1

    @classmethod
    def read(cls, dev: Device) -> Reading:
        main = [c for c in dev.channels if c.usage_page == 0xFF02]
        others = [c for c in dev.channels if c.usage_page >= 0xFF00 and c not in main]
        last = None
        for ch in main + others:
            try:
                return cls._read_from(ch)
            except WriteError as e:  # canal recusou o pedido: tenta a próxima coleção
                last = e
        raise last or OSError("sem canal de comando")

    @classmethod
    def _read_from(cls, ch: Channel) -> Reading:
        with Handle(ch.path) as h:
            h.drain()
            deadline = time.time() + 4
            next_poke, pokes, connected, off_in_a_row = 0.0, 0, None, 0
            while time.time() < deadline:
                if time.time() >= next_poke:
                    h.write(cls.STATUS)
                    if connected or (connected is None and pokes > 0):
                        h.write(cls.BATTERY)
                    pokes += 1
                    next_poke = time.time() + 0.5
                f = h.read(100)
                r = cls.parse_battery(f)
                if r:
                    return r
                st = cls.parse_status(f)
                if st is True:
                    if not connected:
                        next_poke = 0
                    connected, off_in_a_row = True, 0
                elif st is False:
                    connected = False
                    off_in_a_row += 1
                    if off_in_a_row >= 2:
                        raise DeviceOff("mouse desligado")
        raise TimeoutError("mouse não respondeu (dormindo?)")


# --------------------------------------------------------------------------- AULA (receptor Compx 3554:fa09)
# Quadros de 20 bytes no relatório 0x13; último byte = soma dos 19 anteriores.

class Compx:
    kind = "keyboard"
    VID, PID, LENGTH = 0x3554, 0xFA09, 20

    @classmethod
    def frame(cls, cmd, arg):
        f = [0] * cls.LENGTH
        f[0], f[1], f[2] = 0x13, cmd, arg
        f[-1] = sum(f) & 0xFF
        return f

    @classmethod
    def requests(cls):
        return [cls.frame(0x05, 0x01), cls.frame(0x4A, 0x01), cls.frame(0x4A, 0x00)]

    @classmethod
    def channel(cls, dev):
        for c in dev.channels:
            if c.usage_page == 0xFF02 and c.usage in (0, 2):
                return c
        return None

    @classmethod
    def matches(cls, dev: Device) -> bool:
        return dev.vendor_id == cls.VID and dev.product_id == cls.PID and cls.channel(dev) is not None

    @classmethod
    def parse(cls, f):
        if len(f) < cls.LENGTH or f[0] != 0x13 or (sum(f[:19]) & 0xFF) != f[19]:
            return None
        level = flags = None
        if (f[1] & 0x7F) == 0x4A and f[2] == 0x01 and f[3] == 0x00 and f[4] == 0x02:
            level, flags = f[5], f[6]
        elif f[1] == 0x0A and f[2] == 0x01 and f[4] == 0x04 and f[5] == 0x05:
            level, flags = f[6], f[7]
        elif f[1] == 0x05 and f[2] == 0x01 and f[4] == 0x0A:
            return "ack"
        if level is None or not (1 <= level <= 100):
            return None
        return Reading(capacity=level, charging=bool(flags & 0x10))

    @classmethod
    def read(cls, dev: Device) -> Reading:
        ch = cls.channel(dev)
        with Handle(ch.path) as h:
            h.drain()
            answered = False

            def listen(seconds, poke):
                nonlocal answered
                end, next_poke = time.time() + seconds, 0.0
                while time.time() < end:
                    if poke and time.time() >= next_poke:
                        for r in cls.requests():
                            try:
                                h.write(r)
                            except OSError:
                                pass
                        next_poke = time.time() + 0.5
                    p = cls.parse(h.read(100))
                    if p == "ack":
                        answered = True
                    elif p:
                        return p
                return None

            r = listen(1.5, True)
            if r:
                return r
            # Plano B: mesmos pedidos via HidD_SetOutputReport, lendo a resposta neste handle.
            t = threading.Thread(target=set_output_report, args=(ch.path, cls.requests()), daemon=True)
            t.start()
            r = listen(2.5, False)
            t.join(3)
            if r:
                return r
        raise TimeoutError("teclado não respondeu" if answered else "receptor não respondeu")


# --------------------------------------------------------------------------- Corsair Virtuoso / Void

def _bragi_step(prefix, target, prop, parse):
    length = 65 if prefix else 64
    request = pad(prefix + [0x02, target, 0x02, prop, 0x00], length)

    def accept(f):
        if len(f) < 6 or f[0] != 0x01 or f[2] != 0x02:
            return None
        if f[1] != (0x01 if target == 0x09 else 0x00):
            return "off" if f[3] == 0x05 else None
        if f[3] == 0x05:
            return "off"
        return parse(f[4] | (f[5] << 8)) if f[3] == 0x00 else None
    return request, accept


def _bragi_battery(v):
    return Reading(capacity=min(100, max(0, round(v / 10)))) if 1 <= v <= 1000 else None


def _void_accept(f):
    if len(f) < 5 or f[0] != 100:
        return None
    if f[4] == 0:
        return "off"
    return Reading(capacity=min(100, f[2] & 0x7F), charging=f[4] in (4, 5))


def _bragi_status(v):
    # propriedade 0x10: 1 = carregando, 2 = descarregando, 3 = carga completa (no cabo)
    return Reading(charging=v in (1, 3)) if v in (1, 2, 3) else None


_ok = lambda v: Reading()  # passos de "acordar" sem valor útil

CORSAIR_ENTRIES = [
    {   # Virtuoso MAX, Void Wireless v2, HS80 MAX
        "pids": {0x2A08, 0x0A97, 0x2A02}, "page": 0xFF42, "usage": None, "iface": None,
        "steps": [_bragi_step([0x00], 0x08, 0x13, _ok), _bragi_step([0x00], 0x08, 0x12, _ok),
                  _bragi_step([0x00], 0x09, 0x12, _ok), _bragi_step([0x00], 0x09, 0x0F, _bragi_battery)],
        "optional": [_bragi_step([0x00], 0x09, 0x10, _bragi_status)],
    },
    {   # Virtuoso SE / XT
        "pids": {0x0A3E, 0x0A3D, 0x0A40, 0x0A64}, "page": 0xFF42, "usage": 0x01, "iface": None,
        "steps": [_bragi_step([], 0x09, 0x0F, _bragi_battery)],
        "optional": [_bragi_step([], 0x09, 0x10, _bragi_status)],
    },
    {   # Virtuoso RGB Wireless original e Void
        "pids": {0x1B1C, 0x1B27, 0x0A14, 0x0A16, 0x0A17, 0x0A1D, 0x0A1A, 0x1B2A, 0x1B23, 0x1B29,
                 0x0A55, 0x0A51, 0x0A52, 0x0A38, 0x0A4F, 0x0A2B, 0x0A75, 0x0A56},
        "page": 0xFFC5, "usage": 0x01, "iface": 3,
        "steps": [([0xC9, 0x64], _void_accept)],
    },
]


class Corsair:
    kind = "headset"
    VID = 0x1B1C

    @classmethod
    def entry(cls, dev):
        if dev.vendor_id != cls.VID:
            return None
        for e in CORSAIR_ENTRIES:
            if dev.product_id in e["pids"]:
                return e
        return None

    @classmethod
    def channels(cls, dev, e):
        return [c for c in dev.channels if c.usage_page == e["page"]
                and (e["usage"] is None or c.usage == e["usage"])
                and (e["iface"] is None or c.interface == e["iface"])]

    @classmethod
    def matches(cls, dev):
        e = cls.entry(dev)
        return bool(e and cls.channels(dev, e))

    @classmethod
    def read(cls, dev: Device) -> Reading:
        e = cls.entry(dev)
        last = TimeoutError("fone não respondeu")
        for ch in cls.channels(dev, e):
            try:
                with Handle(ch.path) as h:
                    result = Reading()
                    for request, accept in e["steps"]:
                        h.drain()
                        h.write(request)
                        end, part = time.time() + 1.5, None
                        while time.time() < end and part is None:
                            part = accept(h.read(100))
                        if part is None:
                            raise TimeoutError("fone não respondeu")
                        if part == "off":
                            raise DeviceOff("fone desligado")
                        if part.capacity is not None:
                            result.capacity = part.capacity
                        if part.charging is not None:
                            result.charging = part.charging
                    # extras que não derrubam a leitura se o fone não responder (ex.: carregando)
                    for request, accept in e.get("optional", []):
                        try:
                            h.drain()
                            h.write(request)
                            end, part = time.time() + 1.0, None
                            while time.time() < end and part is None:
                                part = accept(h.read(100))
                            if isinstance(part, Reading) and part.charging is not None:
                                result.charging = part.charging
                        except OSError:
                            pass
                    if result.capacity is not None:
                        return result
            except DeviceOff:
                raise
            except Exception as ex:
                last = ex
        raise last


DRIVERS = [Compx, Kysona, Corsair]


def driver_for(dev: Device):
    for d in DRIVERS:
        if d.matches(dev):
            return d
    return None


def read_device(dev: Device) -> Reading:
    drv = driver_for(dev)
    try:
        return drv.read(dev)
    except DeviceOff as e:
        return Reading(offline=True, error=str(e))
    except Exception as e:
        return Reading(offline=True, error=str(e) or type(e).__name__)


# --------------------------------------------------------------------------- Limites e notificações

def thresholds_to_notify(level: int, charging: bool, notified: set, limits: list[int], hysteresis: int):
    """Devolve (limite a avisar ou None, novo conjunto de limites já avisados).
    Avisa uma vez por limite; se pular vários de uma vez (60%→20%), avisa só o mais baixo.
    O aviso 'rearma' quando carrega ou quando o nível volta acima de limite + histerese."""
    notified = set(notified)
    if charging:
        return None, set()
    for t in list(notified):
        if level > t + hysteresis:
            notified.discard(t)
    crossed = [t for t in limits if level <= t and t not in notified]
    if not crossed:
        return None, notified
    lowest = min(crossed)
    notified.update(t for t in limits if t >= lowest)
    return lowest, notified


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(default, dict):
            return {**default, **data}
        return data
    except Exception:
        return json.loads(json.dumps(default))


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- Ícones

def level_color(level, charging, offline):
    """Verde acima de 50 · amarelo 30-50 · laranja 15-29 · vermelho abaixo de 15."""
    if offline or level is None:
        return (110, 110, 110)
    if charging:
        return (40, 120, 220)
    if level > 50:
        return (46, 160, 67)
    if level >= 30:
        return (212, 167, 44)
    if level >= 15:
        return (230, 110, 30)
    return (210, 40, 40)


def _font(size):
    from PIL import ImageFont
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(level, charging, offline, kind):
    """Quadrado na cor do nível com a porcentagem; um raio no canto quando está carregando."""
    from PIL import Image, ImageDraw
    S = 4  # desenha em 256 px e reduz, para bordas suaves
    img = Image.new("RGBA", (64 * S, 64 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 63 * S, 63 * S), radius=12 * S, fill=level_color(level, False, offline))
    text = "?" if level is None else str(level)
    chg = charging and not offline
    if chg:  # número menor para caber o raio do lado direito
        font = _font((34 if len(text) <= 1 else 30 if len(text) == 2 else 22) * S)
    else:
        font = _font((40 if len(text) <= 2 else 30) * S)
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx = (23 if chg else 32) * S
    d.text((cx - w / 2 - bbox[0], (60 * S - h) / 2 - bbox[1] - 2 * S), text, font=font, fill="white")
    if chg:
        bolt = [(52, 3), (40, 30), (49, 30), (45, 52), (61, 22), (52, 22), (58, 3)]
        d.polygon([(x * S, y * S) for x, y in bolt], fill=(255, 230, 70, 255), outline=(30, 20, 0, 255), width=3 * S)
    # letra do tipo no canto (M/T/F) para distinguir os ícones
    d.text((47 * S, 46 * S), {"mouse": "M", "keyboard": "T", "headset": "F"}[kind], font=_font(14 * S), fill=(255, 255, 255, 230))
    return img.resize((64, 64), Image.LANCZOS)


# --------------------------------------------------------------------------- Início com o Windows

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enable: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


# --------------------------------------------------------------------------- App de bandeja

class TrayApp:
    def __init__(self):
        self.config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        if not os.path.exists(CONFIG_PATH):
            save_json(CONFIG_PATH, self.config)
        self.state = load_json(STATE_PATH, {})   # key -> {"avisados": [...], "nivel": n, "hora": "..."}
        self.icons = {}                           # key -> pystray.Icon
        self.refresh_now = threading.Event()
        self.quit = threading.Event()

    def label(self, dev, kind):
        custom = self.config.get("nomes", {}).get(dev.key)
        return custom or f"{KIND_LABEL[kind]} ({dev.name})"

    def menu(self):
        import pystray
        return pystray.Menu(
            pystray.MenuItem("Atualizar agora", lambda: self.refresh_now.set(), default=True),
            pystray.MenuItem("Iniciar com o Windows", self.toggle_autostart,
                             checked=lambda item: autostart_enabled()),
            pystray.MenuItem("Abrir configuração", lambda: os.startfile(CONFIG_PATH)),
            pystray.MenuItem("Sair", self.stop),
        )

    def toggle_autostart(self, icon, item):
        set_autostart(not autostart_enabled())

    def stop(self, *_):
        self.quit.set()
        self.refresh_now.set()
        for ic in list(self.icons.values()):
            ic.stop()

    def ensure_icon(self, key, title, image):
        import pystray
        ic = self.icons.get(key)
        if ic is None:
            ic = pystray.Icon(f"{APP_NAME}-{key}", image, title[:127], self.menu())
            self.icons[key] = ic
            threading.Thread(target=ic.run, daemon=True).start()
            time.sleep(0.3)
        else:
            ic.icon = image
            ic.title = title[:127]
        return ic

    def update_device(self, dev, drv, r: Reading):
        key, kind = dev.key, drv.kind
        name = self.label(dev, kind)
        st = self.state.setdefault(key, {"avisados": []})
        if r.capacity is not None:
            st["nivel"], st["carregando"] = r.capacity, bool(r.charging)
            st["hora"] = datetime.now().strftime("%H:%M")
        level = st.get("nivel")
        if r.offline:
            extra = f" — sem resposta ({r.error})" if level is None else f" — última leitura {st.get('hora')}"
            title = f"{name}: {'?' if level is None else f'{level}%'}{extra}"
        else:
            title = f"{name}: {level}%" + (" (carregando)" if r.charging else "")
        ic = self.ensure_icon(key, title, make_icon(level, bool(st.get("carregando")) and not r.offline, r.offline, kind))

        if r.capacity is not None:
            limit, notified = thresholds_to_notify(
                r.capacity, bool(r.charging), set(st.get("avisados", [])),
                self.config["limites"], self.config.get("histerese", 5))
            st["avisados"] = sorted(notified)
            if limit is not None:
                msg = f"{name} está com {r.capacity}% de bateria."
                if limit <= 10:
                    msg += " Coloque para carregar agora."
                try:
                    ic.notify(msg, f"Bateria {KIND_LABEL[kind].lower()} abaixo de {limit}%")
                except Exception:
                    pass
        save_json(STATE_PATH, self.state)

    def poll_loop(self):
        while not self.quit.is_set():
            try:
                for dev in list_devices():
                    drv = driver_for(dev)
                    if drv:
                        self.update_device(dev, drv, read_device(dev))
            except Exception as e:
                print("erro no ciclo:", e, file=sys.stderr)
            self.refresh_now.wait(self.config.get("intervalo_segundos", 300))
            self.refresh_now.clear()

    def run(self):
        threading.Thread(target=self.poll_loop, daemon=True).start()
        while not self.quit.wait(1):
            pass


# --------------------------------------------------------------------------- Diagnóstico

def scan():
    devs = list_devices()
    print(f"{len(devs)} dispositivos HID encontrados.\n")
    for dev in sorted(devs, key=lambda d: d.key):
        drv = driver_for(dev)
        vendor_pages = sorted({f"{c.usage_page:04x}/{c.usage:04x}" for c in dev.channels if c.usage_page >= 0xFF00})
        if not drv and not vendor_pages:
            continue
        print(f"[{dev.key}] {dev.name}")
        print(f"    coleções de fabricante: {', '.join(vendor_pages) or '-'}")
        if drv:
            r = read_device(dev)
            if r.capacity is not None:
                print(f"    >> {KIND_LABEL[drv.kind]}: {r.capacity}%{' (carregando)' if r.charging else ''}")
            else:
                print(f"    >> {KIND_LABEL[drv.kind]}: sem leitura ({r.error})")
        else:
            print("    (não reconhecido)")
        print()


_mutex = None


def single_instance() -> bool:
    """Garante uma única cópia rodando na bandeja (mutex nomeado do Windows)."""
    global _mutex
    if sys.platform != "win32":
        return True
    k32 = ctypes.windll.kernel32
    _mutex = k32.CreateMutexW(None, False, f"Local\\{APP_NAME}")
    return k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    p = argparse.ArgumentParser(description="Bateria dos periféricos na bandeja do Windows")
    p.add_argument("--scan", action="store_true", help="lista dispositivos e tenta ler a bateria")
    args = p.parse_args()
    if hid is None:
        sys.exit("Instale as dependências: pip install -r requirements.txt")
    if args.scan:
        scan()
    else:
        if not single_instance():
            sys.exit(0)  # já tem um rodando: não cria ícones duplicados
        TrayApp().run()


if __name__ == "__main__":
    main()
