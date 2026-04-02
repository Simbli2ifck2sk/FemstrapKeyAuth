
# have fun skidding
import requests
import ctypes
from ctypes import sizeof, byref, c_void_p, c_size_t, windll, wintypes, POINTER
import pymem
import pymem.process
import time
import win32api
import numpy as np
import pyMeow as pme
import math
import win32con
import dearpygui.dearpygui as dpg
import threading
import json
import re
import win32gui
from numpy import array, float32, linalg
import os
from pathlib import Path
import subprocess
import pyclipper
os.system("title PhantomHook")

NTSTATUS = ctypes.c_long


PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

ntdll = ctypes.WinDLL('ntdll')

NtReadVirtualMemory = ntdll.NtReadVirtualMemory
NtReadVirtualMemory.restype = NTSTATUS
NtReadVirtualMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    POINTER(ctypes.c_size_t)
]

NtWriteVirtualMemory = ntdll.NtWriteVirtualMemory
NtWriteVirtualMemory.restype = NTSTATUS
NtWriteVirtualMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    POINTER(ctypes.c_size_t)
]

class MemoryManager:

    def __init__(self, pid):
        self.process_id = pid
        self.process_handle = None
        self.base_address = None
        self._open_process()

    def _open_process(self):
        if self.process_id is None:
            raise Exception("Process ID is None")

        self.process_handle = windll.kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
            False,
            self.process_id
        )

        if not self.process_handle:
            raise Exception(f"Failed to open process")

        self.base_address = get_module_base(self.process_id)
        if not self.base_address:
            raise Exception("Failed to get base address")

    def read_bytes(self, address, size):
        if not self.process_handle:
            raise Exception("Process not opened")

        buffer = (ctypes.c_byte * size)()
        bytes_read = ctypes.c_size_t(0)

        status = NtReadVirtualMemory(
            self.process_handle,
            ctypes.c_void_p(address),
            ctypes.byref(buffer),
            size,
            ctypes.byref(bytes_read)
        )

        if status != 0:
            raise Exception(f"NtReadVirtualMemory failed with status: {hex(status)}")

        return bytes(buffer)

    def write_bytes(self, address, data, size):
        if not self.process_handle:
            raise Exception("Process not opened")

        if isinstance(data, bytes):
            buffer = (ctypes.c_byte * len(data)).from_buffer_copy(data)
        else:
            buffer = (ctypes.c_byte * size).from_buffer_copy(data)

        bytes_written = ctypes.c_size_t(0)

        status = NtWriteVirtualMemory(
            self.process_handle,
            ctypes.c_void_p(address),
            ctypes.byref(buffer),
            size,
            ctypes.byref(bytes_written)
        )

        if status != 0:
            raise Exception(f"NtWriteVirtualMemory failed with status: {hex(status)}")

        return bytes_written.value

    def read_int(self, address):
        data = self.read_bytes(address, 4)
        return int.from_bytes(data, byteorder='little', signed=True)

    def read_longlong(self, address):
        data = self.read_bytes(address, 8)
        return int.from_bytes(data, byteorder='little', signed=True)

    def read_float(self, address):
        data = self.read_bytes(address, 4)
        return np.frombuffer(data, dtype=np.float32)[0]

    def read_ushort(self, address):
        data = self.read_bytes(address, 2)
        return int.from_bytes(data, byteorder='little', signed=False)

    def write_float(self, address, value):
        data = np.array([value], dtype=np.float32).tobytes()
        return self.write_bytes(address, data, 4)

    def write_short(self, address, value):
        data = value.to_bytes(2, byteorder='little', signed=True)
        return self.write_bytes(address, data, 2)

    def write_ushort(self, address, value):
        data = value.to_bytes(2, byteorder='little', signed=False)
        return self.write_bytes(address, data, 2)

    def read_string(self, address, length):
        data = self.read_bytes(address, length)
        try:
            return data.decode('utf-8').rstrip('\x00')
        except:
            return data.decode('latin-1').rstrip('\x00')

    def close(self):
        if self.process_handle:
            windll.kernel32.CloseHandle(self.process_handle)
            self.process_handle = None

_ADDR_MIN = 0x10000
_ADDR_MAX = 0x7FFFFFFFFFFF

def is_valid_addr(addr) -> bool:
    try:
        return isinstance(addr, int) and _ADDR_MIN <= addr <= _ADDR_MAX
    except Exception:
        return False

def is_valid_position(pos) -> bool:
    try:
        if pos is None:
            return False
        return (len(pos) >= 3
                and all(math.isfinite(float(v)) for v in pos[:3])
                and all(abs(float(v)) < 1e7 for v in pos[:3]))
    except Exception:
        return False

def is_valid_view_matrix(vm) -> bool:
    try:
        if vm is None or vm.shape != (4, 4):
            return False
        if not np.all(np.isfinite(vm)):
            return False
        if np.all(vm[:, 3] == 0):
            return False
        return True
    except Exception:
        return False

def clamp(value, lo, hi):
    return max(lo, min(hi, value))

def safe_read_float(read_fn, fallback=0.0):
    try:
        v = read_fn()
        return v if math.isfinite(float(v)) else fallback
    except Exception:
        return fallback

pm = None

sticky_aim_enabled = False
sticky_target = None
sticky_target_id = None

last_aimbot_state = False


aimbot_trigger_mode = "Hold"
aimbot_toggled = False
_last_aimbot_key_down = False

sensitivity_multiplier = 1.0

hide_console = False
streamproof = False

def get_config_dir():

    home_dir = os.path.expanduser('~')
    config_dir = Path(home_dir) / ".PhantomHook" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir

def toggle_console(hide):
    global hide_console
    hide_console = hide

    try:
        console_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if console_hwnd:
            if hide:
                ctypes.windll.user32.ShowWindow(console_hwnd, 0)
            else:
                ctypes.windll.user32.ShowWindow(console_hwnd, 5)

            if streamproof:
                WDA_EXCLUDEFROMCAPTURE = 0x00000011
                ctypes.windll.user32.SetWindowDisplayAffinity(console_hwnd, WDA_EXCLUDEFROMCAPTURE)

    except Exception as e:
        print(f"Failed to toggle console: {e}")

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def toggle_streamproof(enabled):
    global streamproof
    streamproof = enabled

    if enabled and not is_admin():
        show_admin_warning()
        if dpg.does_item_exist("streamproof_checkbox"):
            dpg.set_value("streamproof_checkbox", False)
        streamproof = False
        return

    try:
        WDA_EXCLUDEFROMCAPTURE = 0x00000011
        affinity = WDA_EXCLUDEFROMCAPTURE if enabled else 0

        console_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if console_hwnd:
            ctypes.windll.user32.SetWindowDisplayAffinity(console_hwnd, affinity)

        menu_hwnd = ctypes.windll.user32.GetForegroundWindow()
        ctypes.windll.user32.SetWindowDisplayAffinity(menu_hwnd, affinity)

        overlay_hwnd = win32gui.FindWindow(None, "Overlay")
        if overlay_hwnd:
            ctypes.windll.user32.SetWindowDisplayAffinity(overlay_hwnd, affinity)
        else:
            pass

        status = "enabled" if enabled else "disabled"
    except Exception as e:
        print(f"Failed to toggle StreamProof: {e}")

def show_admin_warning():
    if dpg.does_item_exist("admin_warning_modal"):
        dpg.delete_item("admin_warning_modal")

    with dpg.window(label="Admin Required", modal=True, show=True, tag="admin_warning_modal",
                    no_resize=True, no_move=True, no_collapse=True, width=450, height=400):
        dpg.add_spacer(height=10)
        dpg.add_text("StreamProof requires Administrator!", color=(255, 100, 100))
        dpg.add_spacer(height=10)
        dpg.add_separator()
        dpg.add_spacer(height=10)
        dpg.add_text("To use StreamProof mode:", color=(180, 180, 200))
        dpg.add_spacer(height=5)
        dpg.add_text("  1. Close this application", indent=20, color=(150, 150, 180))
        dpg.add_text("  2. Right-click PhantomHook.exe", indent=20, color=(150, 150, 180))
        dpg.add_text("  3. Select 'Run as administrator'", indent=20, color=(150, 150, 180))
        dpg.add_spacer(height=15)

        dpg.set_item_pos("admin_warning_modal",
                        [dpg.get_viewport_width() // 2 - 224,
                         dpg.get_viewport_height() // 2 - 200])

def get_roblox_window_rect():

    try:

        hwnd = win32gui.FindWindow(None, "Roblox")
        if hwnd:
            rect = win32gui.GetWindowRect(hwnd)

            x = rect[0]
            y = rect[1]
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            return x, y, width, height
        else:

            sw = win32api.GetSystemMetrics(0)
            sh = win32api.GetSystemMetrics(1)
            return 0, 0, sw, sh
    except:

        sw = win32api.GetSystemMetrics(0)
        sh = win32api.GetSystemMetrics(1)
        return 0, 0, sw, sh
def get_current_settings():
    return {
        "aimbot": {
            "enabled": enable_aimbot,
            "keybind": aimbot_keybind,
            "fov": fov_radius,
            "smoothing": smoothing_factor,
            "aim_mode": aim_mode,
            "use_fov": use_fov,
            "use_smooth": use_smooth,
            "hitchance": silent_hitchance,
            "legit_mode": legit_mode,
            "hit_parts": hit_parts,
            "sticky_aim": sticky_aim_enabled,
            "sensitivity": sensitivity_multiplier,
            "trigger_mode": aimbot_trigger_mode,
        },
        "visuals": {
            "box": enable_esp_box,
            "box_mode": box_mode,
            "skeleton": enable_esp_skeleton,
            "health_bar": enable_health_bar,
            "name": enable_text,
            "fov_circle": enable_fov_circle,
            "crosshair": enable_crosshair,
            "chams": enable_chams,
            "chams_mode": chams_mode,
            "tool": enable_tool,
        },
        "colors": {
            "box_color": esp_box_color,
            "box_outline": esp_box_outline_color,
            "skeleton_color": esp_skeleton_color,
            "skeleton_outline": esp_skeleton_outline_color,
            "health_outline": health_bar_outline_color,
            "crosshair_color": crosshair_color,
            "crosshair_outline": crosshair_outline_color,
            "fov_color": fov_circle_color,
            "fov_outline": fov_circle_outline_color,
            "text_color": text_color,
            "chams_color": chams_color,
            "chams_transparency": chams_transparency,
            "tool_color": tool_color,

        },
        "movement": {
            "fly_enabled": fly_enabled,
            "fly_keybind": fly_keybind,
            "fly_mode": fly_mode,
            "fly_speed": fly_speed,
            "fly_method": fly_method,
            "orbit_enabled": orbit_enabled,
            "orbit_distance": orbit_distance,
            "orbit_speed": orbit_speed,
            "kill_all_enabled": kill_all_enabled,
            "kill_all_keybind": kill_all_keybind,
            "kill_all_trigger_mode": kill_all_trigger_mode,
        },
        "misc": {
            "team_check": team_check,
            "hide_console": hide_console,
            "streamproof": streamproof,
            "distance_check_enabled": distance_check_enabled,
            "max_render_distance": max_render_distance
        },
        "menu": {
            "keybind": menu_keybind
        }
    }
def open_config_folder():

    try:
        config_dir = get_config_dir()

        subprocess.Popen(f'explorer "{config_dir}"')

    except Exception as e:
        print(f"Failed to open config folder: {e}")

def apply_settings(config):
    global enable_aimbot, aimbot_keybind, fov_radius, smoothing_factor
    global enable_esp_box, box_mode, enable_esp_skeleton, enable_health_bar, enable_text
    global enable_fov_circle, enable_crosshair, team_check
    global esp_box_color, esp_box_outline_color, esp_skeleton_color
    global esp_skeleton_outline_color, health_bar_outline_color
    global crosshair_color, crosshair_outline_color
    global fov_circle_color, fov_circle_outline_color, text_color
    global fly_enabled, fly_keybind, fly_mode, fly_speed, aim_mode
    global orbit_enabled, orbit_distance, orbit_speed, orbit_angle
    global use_fov, fly_method, use_smooth
    global menu_keybind
    global enable_chams, chams_color, chams_transparency
    global chams_mode
    global enable_tool, tool_color
    global hide_console, streamproof, distance_check_enabled, max_render_distance
    global silent_hitchance, legit_mode, sticky_aim_enabled, hit_parts, sensitivity_multiplier
    global aimbot_trigger_mode
    global kill_all_enabled, kill_all_keybind, kill_all_trigger_mode
    if "movement" in config:
        fly_enabled = config["movement"].get("fly_enabled", fly_enabled)
        fly_keybind = config["movement"].get("fly_keybind", fly_keybind)
        fly_mode = config["movement"].get("fly_mode", fly_mode)
        fly_speed = config["movement"].get("fly_speed", fly_speed)
        fly_method = config["movement"].get("fly_method", fly_method)
        orbit_enabled = config["movement"].get("orbit_enabled", orbit_enabled)
        orbit_distance = config["movement"].get("orbit_distance", orbit_distance)
        orbit_speed = config["movement"].get("orbit_speed", orbit_speed)
        kill_all_enabled = config["movement"].get("kill_all_enabled", kill_all_enabled)
        kill_all_keybind = config["movement"].get("kill_all_keybind", kill_all_keybind)
        kill_all_trigger_mode = config["movement"].get("kill_all_trigger_mode", kill_all_trigger_mode)

    if "aimbot" in config:
        enable_aimbot = config["aimbot"].get("enabled", enable_aimbot)
        aimbot_keybind = config["aimbot"].get("keybind", aimbot_keybind)
        fov_radius = config["aimbot"].get("fov", fov_radius)
        smoothing_factor = config["aimbot"].get("smoothing", smoothing_factor)
        aim_mode = config["aimbot"].get("aim_mode", aim_mode)
        use_fov = config["aimbot"].get("use_fov", use_fov)
        use_smooth = config["aimbot"].get("use_smooth", use_smooth)
        silent_hitchance = config["aimbot"].get("hitchance", silent_hitchance)
        hit_parts = config["aimbot"].get("hit_parts", hit_parts)
        legit_mode = config["aimbot"].get("legit_mode", legit_mode)
        sticky_aim_enabled = config["aimbot"].get("sticky_aim", sticky_aim_enabled)
        sensitivity_multiplier = config["aimbot"].get("sensitivity", sensitivity_multiplier)
        aimbot_trigger_mode = config["aimbot"].get("trigger_mode", aimbot_trigger_mode)

    if "visuals" in config:
        enable_esp_box = config["visuals"].get("box", enable_esp_box)
        box_mode = config["visuals"].get("box_mode", box_mode)
        enable_esp_skeleton = config["visuals"].get("skeleton", enable_esp_skeleton)
        enable_health_bar = config["visuals"].get("health_bar", enable_health_bar)
        enable_text = config["visuals"].get("name", enable_text)
        enable_fov_circle = config["visuals"].get("fov_circle", enable_fov_circle)
        enable_crosshair = config["visuals"].get("crosshair", enable_crosshair)
        enable_chams = config["visuals"].get("chams", enable_chams)
        chams_mode = config["visuals"].get("chams_mode", chams_mode)
        enable_tool = config["visuals"].get("tool", enable_tool)

    if "colors" in config:
        esp_box_color = config["colors"].get("box_color", esp_box_color)
        esp_box_outline_color = config["colors"].get("box_outline", esp_box_outline_color)
        esp_skeleton_color = config["colors"].get("skeleton_color", esp_skeleton_color)
        esp_skeleton_outline_color = config["colors"].get("skeleton_outline", esp_skeleton_outline_color)
        health_bar_outline_color = config["colors"].get("health_outline", health_bar_outline_color)
        tool_color = config["colors"].get("tool_color", tool_color)
        crosshair_color = config["colors"].get("crosshair_color", crosshair_color)
        crosshair_outline_color = config["colors"].get("crosshair_outline", crosshair_outline_color)
        fov_circle_color = config["colors"].get("fov_color", fov_circle_color)
        fov_circle_outline_color = config["colors"].get("fov_outline", fov_circle_outline_color)
        text_color = config["colors"].get("text_color", text_color)
        chams_color = config["colors"].get("chams_color", chams_color)
        chams_transparency = config["colors"].get("chams_transparency", chams_transparency)

    if "misc" in config:
        team_check = config["misc"].get("team_check", team_check)
        hide_console = config["misc"].get("hide_console", hide_console)
        streamproof = config["misc"].get("streamproof", streamproof)
        distance_check_enabled = config["misc"].get("distance_check_enabled", distance_check_enabled)
        max_render_distance = config["misc"].get("max_render_distance", max_render_distance)
        toggle_console(hide_console)
        toggle_streamproof(streamproof)
    if "menu" in config:
        menu_keybind = config["menu"].get("keybind", menu_keybind)

def save_config(config_name):

    try:
        config_dir = get_config_dir()
        config_path = config_dir / f"{config_name}.json"
        settings = get_current_settings()

        with open(config_path, 'w') as f:
            json.dump(settings, f, indent=4)

        return True, f"Config '{config_name}' saved successfully!"
    except Exception as e:
        return False, f"Failed to save config: {str(e)}"

def load_config(config_name):

    try:
        config_dir = get_config_dir()
        config_path = config_dir / f"{config_name}.json"

        if not config_path.exists():
            return False, f"Config '{config_name}' not found!"

        with open(config_path, 'r') as f:
            config = json.load(f)

        apply_settings(config)
        update_ui_from_globals()

        return True, f"Config '{config_name}' loaded successfully!"
    except Exception as e:
        return False, f"Failed to load config: {str(e)}"

def delete_config(config_name):

    try:
        config_dir = get_config_dir()
        config_path = config_dir / f"{config_name}.json"

        if not config_path.exists():
            return False, f"Config '{config_name}' not found!"

        config_path.unlink()
        return True, f"Config '{config_name}' deleted successfully!"
    except Exception as e:
        return False, f"Failed to delete config: {str(e)}"

def get_config_list():

    try:
        config_dir = get_config_dir()
        return [f.stem for f in config_dir.glob("*.json")]
    except:
        return []

def update_ui_from_globals():
    def force_update_toggle_button(tag, is_on):
        if not dpg.does_item_exist(tag):
            return

        if is_on:
            dpg.bind_item_theme(tag, 0)
            dpg.bind_item_theme(tag, "toggle_button_on")
        else:
            dpg.bind_item_theme(tag, 0)
            dpg.bind_item_theme(tag, "toggle_button_off")

    try:
        if dpg.does_item_exist("keybind_button"):
            dpg.configure_item("keybind_button", label=f"{get_key_name(aimbot_keybind)}")
        if dpg.does_item_exist("fov_slider"):
            dpg.set_value("fov_slider", fov_radius // 10)
        if dpg.does_item_exist("smoothing_slider"):
            dpg.set_value("smoothing_slider", smoothing_factor // 10)
        if dpg.does_item_exist("aim_mode_combo"):
            dpg.set_value("aim_mode_combo", aim_mode)
        if dpg.does_item_exist("aim_trigger_combo"):
            dpg.set_value("aim_trigger_combo", aimbot_trigger_mode)
        if dpg.does_item_exist("fly_method_combo"):
            dpg.set_value("fly_method_combo", fly_method)
        if dpg.does_item_exist("sensitivity_slider"):
            dpg.set_value("sensitivity_slider", sensitivity_multiplier)
        if dpg.does_item_exist("silent_hitchance_slider"):
            dpg.set_value("silent_hitchance_slider", silent_hitchance)
    except Exception as e:
        print(f"Error updating aimbot UI: {e}")

    try:
        if dpg.does_item_exist("box_mode_combo"):
            dpg.set_value("box_mode_combo", box_mode)
        if dpg.does_item_exist("chams_mode_combo"):
            dpg.set_value("chams_mode_combo", chams_mode)
    except Exception as e:
        print(f"Error updating visuals UI: {e}")

    try:
        if dpg.does_item_exist("box_color_picker"):
            dpg.set_value("box_color_picker", hex_to_rgba(esp_box_color))
        if dpg.does_item_exist("box_outline_picker"):
            dpg.set_value("box_outline_picker", hex_to_rgba(esp_box_outline_color))
        if dpg.does_item_exist("skeleton_color_picker"):
            dpg.set_value("skeleton_color_picker", hex_to_rgba(esp_skeleton_color))
        if dpg.does_item_exist("skeleton_outline_picker"):
            dpg.set_value("skeleton_outline_picker", hex_to_rgba(esp_skeleton_outline_color))
        if dpg.does_item_exist("health_outline_picker"):
            dpg.set_value("health_outline_picker", hex_to_rgba(health_bar_outline_color))
        if dpg.does_item_exist("crosshair_color_picker"):
            dpg.set_value("crosshair_color_picker", hex_to_rgba(crosshair_color))
        if dpg.does_item_exist("crosshair_outline_picker"):
            dpg.set_value("crosshair_outline_picker", hex_to_rgba(crosshair_outline_color))
        if dpg.does_item_exist("fov_color_picker"):
            dpg.set_value("fov_color_picker", hex_to_rgba(fov_circle_color))
        if dpg.does_item_exist("fov_outline_picker"):
            dpg.set_value("fov_outline_picker", hex_to_rgba(fov_circle_outline_color))
        if dpg.does_item_exist("text_color_picker"):
            dpg.set_value("text_color_picker", hex_to_rgba(text_color))
        if dpg.does_item_exist("chams_color_picker"):
            dpg.set_value("chams_color_picker", hex_to_rgba(chams_color, chams_transparency))
        if dpg.does_item_exist("chams_trans_slider"):
            dpg.set_value("chams_trans_slider", chams_transparency)
        if dpg.does_item_exist("tool_color_picker"):
            dpg.set_value("tool_color_picker", hex_to_rgba(tool_color))
    except Exception as e:
        print(f"Error updating color pickers: {e}")

    try:
        if dpg.does_item_exist("fly_keybind_button"):
            dpg.configure_item("fly_keybind_button", label=f"{get_key_name(fly_keybind)}")
        if dpg.does_item_exist("fly_mode_combo"):
            dpg.set_value("fly_mode_combo", fly_mode)
        if dpg.does_item_exist("fly_speed_slider"):
            dpg.set_value("fly_speed_slider", fly_speed)
        if dpg.does_item_exist("orbit_distance_slider"):
            dpg.set_value("orbit_distance_slider", orbit_distance)
        if dpg.does_item_exist("orbit_speed_slider"):
            dpg.set_value("orbit_speed_slider", orbit_speed)
        if dpg.does_item_exist("kill_all_keybind_button"):
            dpg.configure_item("kill_all_keybind_button", label=f"{get_key_name(kill_all_keybind)}")
        if dpg.does_item_exist("kill_all_trigger_combo"):
            dpg.set_value("kill_all_trigger_combo", kill_all_trigger_mode)
    except Exception as e:
        print(f"Error updating movement UI: {e}")

    try:
        if dpg.does_item_exist("menu_keybind_button"):
            dpg.configure_item("menu_keybind_button", label=f"{get_key_name(menu_keybind)}")
        if dpg.does_item_exist("max_distance_slider"):
            dpg.set_value("max_distance_slider", max_render_distance)
    except Exception as e:
        print(f"Error updating misc UI: {e}")

    try:
        force_update_toggle_button("aimbot_checkbox", enable_aimbot)
        force_update_toggle_button("hide_console_checkbox", hide_console)
        force_update_toggle_button("streamproof_checkbox", streamproof)
        force_update_toggle_button("box_checkbox", enable_esp_box)
        force_update_toggle_button("skeleton_checkbox", enable_esp_skeleton)
        force_update_toggle_button("health_checkbox", enable_health_bar)
        force_update_toggle_button("name_checkbox", enable_text)
        force_update_toggle_button("fov_circle_checkbox", enable_fov_circle)
        force_update_toggle_button("crosshair_checkbox", enable_crosshair)
        force_update_toggle_button("chams_checkbox", enable_chams)
        force_update_toggle_button("tool_checkbox", enable_tool)
        force_update_toggle_button("fly_checkbox", fly_enabled)
        force_update_toggle_button("orbit_checkbox", orbit_enabled)
        force_update_toggle_button("kill_all_checkbox", kill_all_enabled)
        force_update_toggle_button("use_fov_checkbox", use_fov)
        force_update_toggle_button("use_smooth_checkbox", use_smooth)
        force_update_toggle_button("legit_mode_checkbox", legit_mode)
        force_update_toggle_button("sticky_aim_checkbox", sticky_aim_enabled)
        force_update_toggle_button("team_checkbox", team_check)
        force_update_toggle_button("distance_check_checkbox", distance_check_enabled)
    except Exception as e:
        print(f"Error updating button toggles: {e}")

    try:
        hit_parts_lower = [part.lower() if isinstance(part, str) else part for part in hit_parts]
        force_update_toggle_button("hitpart_head", "head" in hit_parts_lower or "Head" in hit_parts)
        force_update_toggle_button("hitpart_arms", "arms" in hit_parts_lower or "Arms" in hit_parts)
        force_update_toggle_button("hitpart_torso", "torso" in hit_parts_lower or "Torso" in hit_parts)
        force_update_toggle_button("hitpart_legs", "legs" in hit_parts_lower or "Legs" in hit_parts)
    except Exception as e:
        print(f"Error updating hit parts: {e}")

def save_config_callback(sender, app_data):

    config_name = dpg.get_value("config_name_input").strip()

    if not config_name:

        return

    success, message = save_config(config_name)

    if success:
        refresh_config_list()

def load_config_callback(sender, app_data):

    selected = dpg.get_value("config_list")

    if not selected:

        return

    success, message = load_config(selected)

def delete_config_callback(sender, app_data):

    selected = dpg.get_value("config_list")

    if not selected:

        return

    success, message = delete_config(selected)

    if success:
        refresh_config_list()

def refresh_config_list():

    configs = get_config_list()
    dpg.configure_item("config_list", items=configs)

print("[+] Loading")

CACHE_TIMEOUT = 0.5

R15_PARTS = {"Head", "UpperTorso", "LowerTorso", "LeftUpperArm", "LeftLowerArm", "LeftHand", "RightUpperArm", "RightLowerArm", "RightHand", "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "RightUpperLeg", "RightLowerLeg", "RightFoot"}
R6_PARTS = {"Head", "Torso", "Left Arm", "Right Arm", "Left Leg", "Right Leg"}

R15_BONES = [("Head", "UpperTorso"), ("UpperTorso", "LowerTorso"), ("UpperTorso", "LeftUpperArm"), ("LeftUpperArm", "LeftLowerArm"), ("LeftLowerArm", "LeftHand"), ("UpperTorso", "RightUpperArm"), ("RightUpperArm", "RightLowerArm"), ("RightLowerArm", "RightHand"), ("LowerTorso", "LeftUpperLeg"), ("LeftUpperLeg", "LeftLowerLeg"), ("LeftLowerLeg", "LeftFoot"), ("LowerTorso", "RightUpperLeg"), ("RightUpperLeg", "RightLowerLeg"), ("RightLowerLeg", "RightFoot")]
R6_BONES = [("Head", "Torso"), ("Torso", "Left Arm"), ("Torso", "Right Arm"), ("Torso", "Left Leg"), ("Torso", "Right Leg")]

_cache = {
    "name": {},
    "character": {},
    "humanoid": {},
    "children": {},
    "drp": {},
    "string": {},
    "class_name": {},
    "localplayer": (0, 0),
    "maxhealth": {},
    "primitive": {},
    "character_full": {},
    "team": {}
}

_last_cache_cleanup = 0

def cleanup_stale_cache():
    global _last_cache_cleanup
    now = time.time()

    if now - _last_cache_cleanup < 2.0:
        return

    _last_cache_cleanup = now

    for cache_name in ["name", "character", "humanoid", "children", "drp", "string", "class_name", "maxhealth", "primitive", "team"]:
        cache = _cache[cache_name]
        stale_keys = [k for k, (v, ts) in cache.items() if now - ts > CACHE_TIMEOUT * 3]
        for k in stale_keys:
            cache.pop(k, None)

    char_cache = _cache["character_full"]
    stale_chars = [k for k, v in char_cache.items() if now - v.get("timestamp", 0) > CACHE_TIMEOUT * 2]
    for k in stale_chars:
        char_cache.pop(k, None)

    stale_geom = [k for k, v in _chams_geometry_cache.items() if now - v.get('time', 0) > 5.0]
    for k in stale_geom:
        _chams_geometry_cache.pop(k, None)

    stale_trans = [k for k, v in _chams_transform_cache.items() if now - v.get('time', 0) > 5.0]
    for k in stale_trans:
        _chams_transform_cache.pop(k, None)

    stale_hrp = [k for k, v in _hrp_position_cache.items() if now - v.get('time', 0) > 1.0]
    for k in stale_hrp:
        _hrp_position_cache.pop(k, None)

def find_roblox_process():
    for p in pymem.process.list_processes():
        try:
            if b"RobloxPlayerBeta.exe" in p.szExeFile:
                print("[+] Found Roblox!")
                return p.th32ProcessID
        except Exception:
            continue
    return None

def get_module_base(pid):
    hProcess = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
    if not hProcess:
        return None
    try:
        hModules = (c_void_p * 1)()
        cbNeeded = c_size_t()
        if ctypes.windll.psapi.EnumProcessModules(hProcess, byref(hModules), sizeof(hModules), byref(cbNeeded)):
            return int(hModules[0])
    finally:
        ctypes.windll.kernel32.CloseHandle(hProcess)
    return None



offsetssupport = """
#include <cstdint>
#include <string>
namespace Offsets {
    inline std::string ClientVersion = "version-760d064d05424689";

    namespace AirProperties {
         inline constexpr uintptr_t AirDensity = 0x18;
         inline constexpr uintptr_t GlobalWind = 0x3c;
    }

    namespace AnimationTrack {
         inline constexpr uintptr_t Animation = 0xd0;
         inline constexpr uintptr_t Animator = 0x118;
         inline constexpr uintptr_t IsPlaying = 0x4b8;
         inline constexpr uintptr_t Looped = 0xf5;
         inline constexpr uintptr_t Speed = 0xe4;
    }

    namespace Animator {
         inline constexpr uintptr_t ActiveAnimations = 0x650;
    }

    namespace Atmosphere {
         inline constexpr uintptr_t Color = 0xd0;
         inline constexpr uintptr_t Decay = 0xdc;
         inline constexpr uintptr_t Density = 0xe8;
         inline constexpr uintptr_t Glare = 0xec;
         inline constexpr uintptr_t Haze = 0xf0;
         inline constexpr uintptr_t Offset = 0xf4;
    }

    namespace Attachment {
         inline constexpr uintptr_t Position = 0xdc;
    }

    namespace BasePart {
         inline constexpr uintptr_t Color3 = 0x194;
         inline constexpr uintptr_t Primitive = 0x148;
         inline constexpr uintptr_t Shape = 0x1b1;
         inline constexpr uintptr_t Transparency = 0xf0;
    }

    namespace BloomEffect {
         inline constexpr uintptr_t Enabled = 0xc8;
         inline constexpr uintptr_t Intensity = 0xd0;
         inline constexpr uintptr_t Size = 0xd4;
         inline constexpr uintptr_t Threshold = 0xd8;
    }

    namespace BlurEffect {
         inline constexpr uintptr_t Enabled = 0xc8;
         inline constexpr uintptr_t Size = 0xd0;
    }

    namespace ByteCode {
         inline constexpr uintptr_t Pointer = 0x10;
         inline constexpr uintptr_t Size = 0x20;
    }

    namespace Camera {
         inline constexpr uintptr_t CameraSubject = 0xe8;
         inline constexpr uintptr_t CameraType = 0x158;
         inline constexpr uintptr_t FieldOfView = 0x160;
         inline constexpr uintptr_t Position = 0x11c;
         inline constexpr uintptr_t Rotation = 0xf8;
         inline constexpr uintptr_t Viewport = 0x2ac;
         inline constexpr uintptr_t ViewportSize = 0x2e8;
    }

    namespace CharacterMesh {
         inline constexpr uintptr_t BaseTextureId = 0xe0;
         inline constexpr uintptr_t BodyPart = 0x160;
         inline constexpr uintptr_t MeshId = 0x110;
         inline constexpr uintptr_t OverlayTextureId = 0x140;
    }

    namespace ClickDetector {
         inline constexpr uintptr_t MaxActivationDistance = 0x100;
         inline constexpr uintptr_t MouseIcon = 0xe0;
    }

    namespace Clothing {
         inline constexpr uintptr_t Color3 = 0x128;
         inline constexpr uintptr_t Template = 0x108;
    }

    namespace ColorCorrectionEffect {
         inline constexpr uintptr_t Brightness = 0xdc;
         inline constexpr uintptr_t Contrast = 0xe0;
         inline constexpr uintptr_t Enabled = 0xc8;
         inline constexpr uintptr_t TintColor = 0xd0;
    }

    namespace ColorGradingEffect {
         inline constexpr uintptr_t Enabled = 0xc8;
         inline constexpr uintptr_t TonemapperPreset = 0xd0;
    }

    namespace DataModel {
         inline constexpr uintptr_t CreatorId = 0x188;
         inline constexpr uintptr_t GameId = 0x190;
         inline constexpr uintptr_t GameLoaded = 0x5f8;
         inline constexpr uintptr_t JobId = 0x138;
         inline constexpr uintptr_t PlaceId = 0x198;
         inline constexpr uintptr_t PlaceVersion = 0x1b4;
         inline constexpr uintptr_t PrimitiveCount = 0x438;
         inline constexpr uintptr_t ScriptContext = 0x3f0;
         inline constexpr uintptr_t ServerIP = 0x5e0;
         inline constexpr uintptr_t Workspace = 0x178;
    }

    namespace DepthOfFieldEffect {
         inline constexpr uintptr_t Enabled = 0xc8;
         inline constexpr uintptr_t FarIntensity = 0xd0;
         inline constexpr uintptr_t FocusDistance = 0xd4;
         inline constexpr uintptr_t InFocusRadius = 0xd8;
         inline constexpr uintptr_t NearIntensity = 0xdc;
    }

    namespace FakeDataModel {
         inline constexpr uintptr_t Pointer = 0x7e83168;
         inline constexpr uintptr_t RealDataModel = 0x1c0;
    }

    namespace GuiBase2D {
         inline constexpr uintptr_t AbsolutePosition = 0x110;
         inline constexpr uintptr_t AbsoluteRotation = 0x188;
         inline constexpr uintptr_t AbsoluteSize = 0x118;
    }

    namespace GuiObject {
         inline constexpr uintptr_t BackgroundColor3 = 0x538;
         inline constexpr uintptr_t BackgroundTransparency = 0x544;
         inline constexpr uintptr_t BorderColor3 = 0x544;
         inline constexpr uintptr_t Image = 0x9f0;
         inline constexpr uintptr_t LayoutOrder = 0x574;
         inline constexpr uintptr_t Position = 0x508;
         inline constexpr uintptr_t RichText = 0xa98;
         inline constexpr uintptr_t Rotation = 0x188;
         inline constexpr uintptr_t ScreenGui_Enabled = 0x4bc;
         inline constexpr uintptr_t Size = 0x528;
         inline constexpr uintptr_t Text = 0xdf8;
         inline constexpr uintptr_t TextColor3 = 0xea8;
         inline constexpr uintptr_t Visible = 0x5a1;
         inline constexpr uintptr_t ZIndex = 0x598;
    }

    namespace Humanoid {
         inline constexpr uintptr_t AutoJumpEnabled = 0x1d8;
         inline constexpr uintptr_t AutoRotate = 0x1d9;
         inline constexpr uintptr_t BreakJointsOnDeath = 0x1db;
         inline constexpr uintptr_t CameraOffset = 0x140;
         inline constexpr uintptr_t DisplayDistanceType = 0x18c;
         inline constexpr uintptr_t DisplayName = 0xd0;
         inline constexpr uintptr_t EvaluateStateMachine = 0x1dc;
         inline constexpr uintptr_t FloorMaterial = 0x190;
         inline constexpr uintptr_t Health = 0x194;
         inline constexpr uintptr_t HealthDisplayDistance = 0x198;
         inline constexpr uintptr_t HealthDisplayType = 0x19c;
         inline constexpr uintptr_t HipHeight = 0x1a0;
         inline constexpr uintptr_t HumanoidRootPart = 0x4c0;
         inline constexpr uintptr_t HumanoidState = 0x8d8;
         inline constexpr uintptr_t HumanoidStateID = 0x20;
         inline constexpr uintptr_t IsWalking = 0x956;
         inline constexpr uintptr_t Jump = 0x1dd;
         inline constexpr uintptr_t JumpHeight = 0x1ac;
         inline constexpr uintptr_t JumpPower = 0x1b0;
         inline constexpr uintptr_t MaxHealth = 0x1b4;
         inline constexpr uintptr_t MaxSlopeAngle = 0x1b8;
         inline constexpr uintptr_t MoveDirection = 0x158;
         inline constexpr uintptr_t MoveToPart = 0x130;
         inline constexpr uintptr_t MoveToPoint = 0x17c;
         inline constexpr uintptr_t NameDisplayDistance = 0x1bc;
         inline constexpr uintptr_t NameOcclusion = 0x1c0;
         inline constexpr uintptr_t PlatformStand = 0x1df;
         inline constexpr uintptr_t RequiresNeck = 0x1e0;
         inline constexpr uintptr_t RigType = 0x1c8;
         inline constexpr uintptr_t SeatPart = 0x120;
         inline constexpr uintptr_t Sit = 0x1e0;
         inline constexpr uintptr_t TargetPoint = 0x164;
         inline constexpr uintptr_t Walkspeed = 0x1d4;
         inline constexpr uintptr_t WalkspeedCheck = 0x3c0;
    }

    namespace Instance {
         inline constexpr uintptr_t AttributeContainer = 0x48;
         inline constexpr uintptr_t AttributeList = 0x18;
         inline constexpr uintptr_t AttributeToNext = 0x58;
         inline constexpr uintptr_t AttributeToValue = 0x18;
         inline constexpr uintptr_t ChildrenEnd = 0x8;
         inline constexpr uintptr_t ChildrenStart = 0x70;
         inline constexpr uintptr_t ClassBase = 0x10b0;
         inline constexpr uintptr_t ClassDescriptor = 0x18;
         inline constexpr uintptr_t ClassName = 0x8;
         inline constexpr uintptr_t Name = 0xb0;
         inline constexpr uintptr_t Parent = 0x68;
         inline constexpr uintptr_t This = 0x8;
    }

    namespace Lighting {
         inline constexpr uintptr_t Ambient = 0xd8;
         inline constexpr uintptr_t Brightness = 0x120;
         inline constexpr uintptr_t ClockTime = 0x1b8;
         inline constexpr uintptr_t ColorShift_Bottom = 0xf0;
         inline constexpr uintptr_t ColorShift_Top = 0xe4;
         inline constexpr uintptr_t EnvironmentDiffuseScale = 0x124;
         inline constexpr uintptr_t EnvironmentSpecularScale = 0x128;
         inline constexpr uintptr_t ExposureCompensation = 0x12c;
         inline constexpr uintptr_t FogColor = 0xfc;
         inline constexpr uintptr_t FogEnd = 0x134;
         inline constexpr uintptr_t FogStart = 0x138;
         inline constexpr uintptr_t GeographicLatitude = 0x190;
         inline constexpr uintptr_t GlobalShadows = 0x148;
         inline constexpr uintptr_t GradientBottom = 0x194;
         inline constexpr uintptr_t GradientTop = 0x150;
         inline constexpr uintptr_t LightColor = 0x15c;
         inline constexpr uintptr_t LightDirection = 0x168;
         inline constexpr uintptr_t MoonPosition = 0x184;
         inline constexpr uintptr_t OutdoorAmbient = 0x108;
         inline constexpr uintptr_t Sky = 0x1d8;
         inline constexpr uintptr_t Source = 0x174;
         inline constexpr uintptr_t SunPosition = 0x178;
    }

    namespace LocalScript {
         inline constexpr uintptr_t ByteCode = 0x1a8;
         inline constexpr uintptr_t GUID = 0xe8;
         inline constexpr uintptr_t Hash = 0x1b8;
    }

    namespace MaterialColors {
         inline constexpr uintptr_t Asphalt = 0x30;
         inline constexpr uintptr_t Basalt = 0x27;
         inline constexpr uintptr_t Brick = 0xf;
         inline constexpr uintptr_t Cobblestone = 0x33;
         inline constexpr uintptr_t Concrete = 0xc;
         inline constexpr uintptr_t CrackedLava = 0x2d;
         inline constexpr uintptr_t Glacier = 0x1b;
         inline constexpr uintptr_t Grass = 0x6;
         inline constexpr uintptr_t Ground = 0x2a;
         inline constexpr uintptr_t Ice = 0x36;
         inline constexpr uintptr_t LeafyGrass = 0x39;
         inline constexpr uintptr_t Limestone = 0x3f;
         inline constexpr uintptr_t Mud = 0x24;
         inline constexpr uintptr_t Pavement = 0x42;
         inline constexpr uintptr_t Rock = 0x18;
         inline constexpr uintptr_t Salt = 0x3c;
         inline constexpr uintptr_t Sand = 0x12;
         inline constexpr uintptr_t Sandstone = 0x21;
         inline constexpr uintptr_t Slate = 0x9;
         inline constexpr uintptr_t Snow = 0x1e;
         inline constexpr uintptr_t WoodPlanks = 0x15;
    }

    namespace MeshPart {
         inline constexpr uintptr_t MeshId = 0x2e8;
         inline constexpr uintptr_t Texture = 0x318;
    }

    namespace Misc {
         inline constexpr uintptr_t Adornee = 0x108;
         inline constexpr uintptr_t AnimationId = 0xd0;
         inline constexpr uintptr_t StringLength = 0x10;
         inline constexpr uintptr_t Value = 0xd0;
    }

    namespace Model {
         inline constexpr uintptr_t PrimaryPart = 0x278;
         inline constexpr uintptr_t Scale = 0x164;
    }

    namespace ModuleScript {
         inline constexpr uintptr_t ByteCode = 0x150;
         inline constexpr uintptr_t GUID = 0xe8;
         inline constexpr uintptr_t Hash = 0x160;
    }

    namespace MouseService {
         inline constexpr uintptr_t InputObject = 0x100;
         inline constexpr uintptr_t MousePosition = 0xec;
         inline constexpr uintptr_t SensitivityPointer = 0x7ef6f60;
    }

    namespace Player {
         inline constexpr uintptr_t CameraMode = 0x318;
         inline constexpr uintptr_t Country = 0x110;
         inline constexpr uintptr_t DisplayName = 0x130;
         inline constexpr uintptr_t Gender = 0x0;
         inline constexpr uintptr_t HealthDisplayDistance = 0x338;
         inline constexpr uintptr_t LocalPlayer = 0x130;
         inline constexpr uintptr_t MaxZoomDistance = 0x310;
         inline constexpr uintptr_t MinZoomDistance = 0x314;
         inline constexpr uintptr_t ModelInstance = 0x380;
         inline constexpr uintptr_t Mouse = 0xf78;
         inline constexpr uintptr_t NameDisplayDistance = 0x344;
         inline constexpr uintptr_t Team = 0x290;
         inline constexpr uintptr_t UserId = 0x2b8;
    }

    namespace PlayerConfigurer {
         inline constexpr uintptr_t Pointer = 0x7e60b10;
    }

    namespace PlayerMouse {
         inline constexpr uintptr_t Icon = 0xe0;
         inline constexpr uintptr_t Workspace = 0x168;
    }

    namespace Primitive {
         inline constexpr uintptr_t AssemblyAngularVelocity = 0xfc;
         inline constexpr uintptr_t AssemblyLinearVelocity = 0xf0;
         inline constexpr uintptr_t Flags = 0x1ae;
         inline constexpr uintptr_t Material = 0x0;
         inline constexpr uintptr_t Owner = 0x210;
         inline constexpr uintptr_t Position = 0xe4;
         inline constexpr uintptr_t Rotation = 0xc0;
         inline constexpr uintptr_t Size = 0x1b0;
         inline constexpr uintptr_t Validate = 0x6;
    }

    namespace PrimitiveFlags {
         inline constexpr uintptr_t Anchored = 0x2;
         inline constexpr uintptr_t CanCollide = 0x8;
         inline constexpr uintptr_t CanTouch = 0x10;
    }

    namespace ProximityPrompt {
         inline constexpr uintptr_t ActionText = 0xd0;
         inline constexpr uintptr_t Enabled = 0x156;
         inline constexpr uintptr_t GamepadKeyCode = 0x13c;
         inline constexpr uintptr_t HoldDuration = 0x140;
         inline constexpr uintptr_t KeyCode = 0x144;
         inline constexpr uintptr_t MaxActivationDistance = 0x148;
         inline constexpr uintptr_t ObjectText = 0xf0;
         inline constexpr uintptr_t RequiresLineOfSight = 0x157;
    }

    namespace RenderJob {
         inline constexpr uintptr_t FakeDataModel = 0x38;
         inline constexpr uintptr_t RealDataModel = 0x1b0;
         inline constexpr uintptr_t RenderView = 0x1d0;
    }

    namespace RenderView {
         inline constexpr uintptr_t DeviceD3D11 = 0x8;
         inline constexpr uintptr_t LightingValid = 0x148;
         inline constexpr uintptr_t SkyValid = 0x2cd;
         inline constexpr uintptr_t VisualEngine = 0x10;
    }

    namespace RunService {
         inline constexpr uintptr_t HeartbeatFPS = 0xb8;
         inline constexpr uintptr_t HeartbeatTask = 0xe8;
    }

    namespace Seat {
         inline constexpr uintptr_t Occupant = 0x220;
    }

    namespace Sky {
         inline constexpr uintptr_t MoonAngularSize = 0x25c;
         inline constexpr uintptr_t MoonTextureId = 0xe0;
         inline constexpr uintptr_t SkyboxBk = 0x110;
         inline constexpr uintptr_t SkyboxDn = 0x140;
         inline constexpr uintptr_t SkyboxFt = 0x170;
         inline constexpr uintptr_t SkyboxLf = 0x1a0;
         inline constexpr uintptr_t SkyboxOrientation = 0x250;
         inline constexpr uintptr_t SkyboxRt = 0x1d0;
         inline constexpr uintptr_t SkyboxUp = 0x200;
         inline constexpr uintptr_t StarCount = 0x260;
         inline constexpr uintptr_t SunAngularSize = 0x254;
         inline constexpr uintptr_t SunTextureId = 0x230;
    }

    namespace Sound {
         inline constexpr uintptr_t Looped = 0x152;
         inline constexpr uintptr_t PlaybackSpeed = 0x130;
         inline constexpr uintptr_t RollOffMaxDistance = 0x134;
         inline constexpr uintptr_t RollOffMinDistance = 0x138;
         inline constexpr uintptr_t SoundGroup = 0x100;
         inline constexpr uintptr_t SoundId = 0xe0;
         inline constexpr uintptr_t Volume = 0x144;
    }

    namespace SpawnLocation {
         inline constexpr uintptr_t AllowTeamChangeOnTouch = 0x45;
         inline constexpr uintptr_t Enabled = 0x1f9;
         inline constexpr uintptr_t ForcefieldDuration = 0x1f0;
         inline constexpr uintptr_t Neutral = 0x1fa;
         inline constexpr uintptr_t TeamColor = 0x1f4;
    }

    namespace SpecialMesh {
         inline constexpr uintptr_t MeshId = 0x108;
         inline constexpr uintptr_t Scale = 0xdc;
    }

    namespace StatsItem {
         inline constexpr uintptr_t Value = 0xc8;
    }

    namespace SunRaysEffect {
         inline constexpr uintptr_t Enabled = 0xc8;
         inline constexpr uintptr_t Intensity = 0xd0;
         inline constexpr uintptr_t Spread = 0xd4;
    }

    namespace TaskScheduler {
         inline constexpr uintptr_t JobEnd = 0xd0;
         inline constexpr uintptr_t JobName = 0x18;
         inline constexpr uintptr_t JobStart = 0xc8;
         inline constexpr uintptr_t MaxFPS = 0xb0;
         inline constexpr uintptr_t Pointer = 0x7f25e08;
    }

    namespace Team {
         inline constexpr uintptr_t BrickColor = 0xd0;
    }

    namespace Terrain {
         inline constexpr uintptr_t GrassLength = 0x1f8;
         inline constexpr uintptr_t MaterialColors = 0x280;
         inline constexpr uintptr_t WaterColor = 0x1e8;
         inline constexpr uintptr_t WaterReflectance = 0x200;
         inline constexpr uintptr_t WaterTransparency = 0x204;
         inline constexpr uintptr_t WaterWaveSize = 0x208;
         inline constexpr uintptr_t WaterWaveSpeed = 0x20c;
    }

    namespace Textures {
         inline constexpr uintptr_t Decal_Texture = 0x198;
         inline constexpr uintptr_t Texture_Texture = 0x198;
    }

    namespace Tool {
         inline constexpr uintptr_t CanBeDropped = 0xdd;
         inline constexpr uintptr_t Enabled = 0x345;
         inline constexpr uintptr_t Grip = 0x494;
         inline constexpr uintptr_t ManualActivationOnly = 0x4a2;
         inline constexpr uintptr_t RequiresHandle = 0x34d;
         inline constexpr uintptr_t TextureId = 0x348;
         inline constexpr uintptr_t Tooltip = 0x450;
    }

    namespace VisualEngine {
         inline constexpr uintptr_t Dimensions = 0x720;
         inline constexpr uintptr_t FakeDataModel = 0x700;
         inline constexpr uintptr_t Pointer = 0x7a36cd8;
         inline constexpr uintptr_t RenderView = 0x800;
         inline constexpr uintptr_t ViewMatrix = 0x120;
    }

    namespace Workspace {
         inline constexpr uintptr_t CurrentCamera = 0x460;
         inline constexpr uintptr_t DistributedGameTime = 0x480;
         inline constexpr uintptr_t ReadOnlyGravity = 0x940;
         inline constexpr uintptr_t World = 0x3d8;
    }

    namespace World {
         inline constexpr uintptr_t AirProperties = 0x1d8;
         inline constexpr uintptr_t FallenPartsDestroyHeight = 0x1c8;
         inline constexpr uintptr_t Gravity = 0x1d0;
         inline constexpr uintptr_t Primitives = 0x240;
         inline constexpr uintptr_t worldStepsPerSec = 0x660;
    }

}
"""
def update_offsets_from_url(cpp_url, json_template_text):
    try:
        
        response = requests.get(cpp_url, timeout=10)
    
        
        response.raise_for_status()
        cpp_offsets_text = response.text

    except:
        cpp_offsets_text = offsetssupport
    parsed_cpp_offsets = {}
    current_namespace = None
    for line in cpp_offsets_text.splitlines():
        line = line.strip()
        namespace_match = re.match(r'namespace (\w+)', line)
        if namespace_match:
            current_namespace = namespace_match.group(1)
            continue
        offset_match = re.match(r'inline constexpr uintptr_t (\w+) = (0x[\da-fA-F]+);', line)
        if offset_match and current_namespace:
            offset_name = offset_match.group(1)
            offset_value = offset_match.group(2)
            parsed_cpp_offsets[f"{current_namespace}::{offset_name}"] = offset_value
        version_match = re.match(r'inline std::string ClientVersion = "([^"]+)";', line)

        if version_match:
            parsed_cpp_offsets["ClientVersion"] = version_match.group(1)
    json_data = json.loads(json_template_text)
    key_mapping = {
        "Adornee": "Misc::Adornee", "Anchored": "PrimitiveFlags::Anchored", "AnimationId": "Misc::AnimationId",
        "AttributeToNext": "Instance::AttributeToNext", "AttributeToValue": "Instance::AttributeToValue",
        "Camera": "Workspace::CurrentCamera", "Gravity": "World::Gravity", "GravityContainer": "Workspace::World",
        "ReadOnlyGravity": "Workspace::ReadOnlyGravity", "CameraMaxZoomDistance": "Player::MaxZoomDistance",
        "CameraMinZoomDistance": "Player::MinZoomDistance", "CameraMode": "Player::CameraMode",
        "CameraPos": "Camera::Position", "CameraRotation": "Camera::Rotation", "CameraSubject": "Camera::CameraSubject",
        "CameraType": "Camera::CameraMode", "CanCollide": "PrimitiveFlags::CanCollide", "CanTouch": "PrimitiveFlags::CanTouch",
        "Children": "Instance::ChildrenStart", "ChildrenEnd": "Instance::ChildrenEnd", "ClassDescriptor": "Instance::ClassDescriptor",
        "ClassDescriptorToClassName": "Instance::ClassName", "ClickDetectorMaxActivationDistance": "ClickDetector::MaxActivationDistance",
        "ClockTime": "Lighting::ClockTime", "CreatorId": "DataModel::CreatorId", "DataModelPrimitiveCount": "DataModel::PrimitiveCount",
        "DecalTexture": "Textures::Decal_Texture", "Dimensions": "VisualEngine::Dimensions", "DisplayName": "Player::DisplayName",
        "FOV": "Camera::FieldOfView", "FakeDataModelPointer": "FakeDataModel::Pointer", "FakeDataModelToDataModel": "FakeDataModel::RealDataModel",
        "FogColor": "Lighting::FogColor", "FogEnd": "Lighting::FogEnd", "FogStart": "Lighting::FogStart",
        "FrameRotation": "GuiObject::Rotation", "FrameSizeX": "GuiObject::Size", "GameId": "DataModel::GameId",
        "GameLoaded": "DataModel::GameLoaded", "Health": "Humanoid::Health",
        "HipHeight": "Humanoid::HipHeight", "HumanoidState": "Humanoid::HumanoidState", "HumanoidStateId": "Humanoid::HumanoidStateID",
        "InputObject": "MouseService::InputObject", "InstanceAttributePointer1": "Instance::AttributeContainer",
        "InstanceAttributePointer2": "Instance::AttributeList", "JobEnd": "TaskScheduler::JobEnd", "JobId": "DataModel::JobId",
        "JobStart": "TaskScheduler::JobStart", "Job_Name": "TaskScheduler::JobName", "JumpPower": "Humanoid::JumpPower",
        "LocalPlayer": "Player::LocalPlayer", "LocalScriptByteCode": "LocalScript::ByteCode", "LocalScriptBytecodePointer": "ByteCode::Pointer",
        "LocalScriptHash": "LocalScript::Hash", "MaxHealth": "Humanoid::MaxHealth", "MaxSlopeAngle": "Humanoid::MaxSlopeAngle",
        "MaterialType": "Primitive::Material", "MeshPartTexture": "MeshPart::Texture", "ModelInstance": "Player::ModelInstance",
        "ModuleScriptByteCode": "ModuleScript::ByteCode", "ModuleScriptBytecodePointer": "ByteCode::Pointer",
        "ModuleScriptHash": "ModuleScript::Hash", "MoonTextureId": "Sky::MoonTextureId",
        "MousePosition": "MouseService::MousePosition", "MouseSensitivity": "MouseService::SensitivityPointer",
        "Name": "Instance::Name", "NameSize": "Misc::StringLength", "OutdoorAmbient": "Lighting::OutdoorAmbient",
        "Parent": "Instance::Parent", "PartSize": "Primitive::Size", "Ping": "StatsItem::Value", "PlaceId": "DataModel::PlaceId",
        "PlayerMouse": "Player::Mouse", "Position": "Primitive::Position", "Primitive": "BasePart::Primitive",
        "PrimitiveValidateValue": "Primitive::Validate", "PrimitivesPointer1": "World::Primitives", "PrimitivesPointer2": "World::Primitives",
        "ProximityPromptActionText": "ProximityPrompt::ActionText", "ProximityPromptEnabled": "ProximityPrompt::Enabled",
        "ProximityPromptGamepadKeyCode": "ProximityPrompt::GamepadKeyCode", "ProximityPromptHoldDuraction": "ProximityPrompt::HoldDuration",
        "ProximityPromptMaxActivationDistance": "ProximityPrompt::MaxActivationDistance", "ProximityPromptMaxObjectText": "ProximityPrompt::ObjectText",
        "RenderJobToFakeDataModel": "TaskScheduler::RenderJobToFakeDataModel", "RenderJobToRenderView": "TaskScheduler::RenderJobToRenderView",
        "RigType": "Humanoid::RigType", "Rotation": "Primitive::Rotation", "ScriptContext": "DataModel::ScriptContext",
        "SkyboxBk": "Sky::SkyboxBk", "SkyboxDn": "Sky::SkyboxDn", "SkyboxFt": "Sky::SkyboxFt", "SkyboxLf": "Sky::SkyboxLf",
        "SkyboxRt": "Sky::SkyboxRt", "SkyboxUp": "Sky::SkyboxUp", "StarCount": "Sky::StarCount", "StringLength": "Misc::StringLength",
        "SunTextureId": "Sky::SunTextureId", "TaskSchedulerMaxFPS": "TaskScheduler::MaxFPS", "TaskSchedulerPointer": "TaskScheduler::Pointer",
        "Team": "Player::Team", "TeamColor": "Team::BrickColor", "TextLabelText": "GuiObject::Text", "TextLabelVisible": "GuiObject::Visible",
        "Transparency": "BasePart::Transparency", "UserId": "Player::UserId", "Value": "Misc::Value", "Velocity": "Primitive::AssemblyLinearVelocity",
        "VisualEngine": "RenderView::VisualEngine", "VisualEnginePointer": "VisualEngine::Pointer", "VisualEngineToDataModel1": "VisualEngine::FakeDataModel",
        "VisualEngineToDataModel2": "FakeDataModel::RealDataModel", "WalkSpeed": "Humanoid::Walkspeed", "WalkSpeedCheck": "Humanoid::WalkspeedCheck",
        "Workspace": "DataModel::Workspace", "WorkspaceToWorld": "Workspace::World", "viewmatrix": "VisualEngine::ViewMatrix",
    }
    if "ClientVersion" in parsed_cpp_offsets:
        json_data["RobloxVersion"] = f"Roblox Version: {parsed_cpp_offsets['ClientVersion']}"

    for json_key, cpp_key in key_mapping.items():
        if cpp_key in parsed_cpp_offsets:
            json_data[json_key] = parsed_cpp_offsets[cpp_key]
    return json.dumps(json_data, indent=2)
offsets_url = "https://imtheo.lol/Offsets/Offsets.hpp"
json_template = """
    {
      "RobloxVersion": "Roblox Version: version-bd08027bb04e4045", "ByfronVersion": "Byfron Version: ???", "Adornee": "0x108",
      "Anchored": "0x2", "AnchoredMask": "0x2", "AnimationId": "0xd0", "AttributeToNext": "0x58", "AttributeToValue": "0x18",
      "AutoJumpEnabled": "0x1DB", "BeamBrightness": "0x190", "BeamColor": "0x120", "BeamLightEmission": "0x19C", "BeamLightInfuence": "0x1A0",
      "CFrame": "0x90", "Camera": "0x4a0", "CameraMaxZoomDistance": "0x310", "CameraMinZoomDistance": "0x314", "CameraMode": "0x318",
      "CameraPos": "0x11c", "CameraRotation": "0xf8", "CameraSubject": "0xe8", "CameraType": "0x158", "CanCollide": "0x8", "CanCollideMask": "0x8",
      "CanTouch": "0x10", "CanTouchMask": "0x10", "CharacterAppearanceId": "0x298", "Children": "0x70", "ChildrenEnd": "0x8",
      "ClassDescriptor": "0x18", "ClassDescriptorToClassName": "0x8", "ClickDetectorMaxActivationDistance": "0x100", "ClockTime": "0x1b8",
      "CreatorId": "0x188", "DataModelDeleterPointer": "0x73A7090", "DataModelPrimitiveCount": "0x438", "DataModelToRenderView1": "0x1D0",
      "DataModelToRenderView2": "0x8", "DataModelToRenderView3": "0x28", "DecalTexture": "0x198", "Deleter": "0x10", "DeleterBack": "0x18",
      "Dimensions": "0x720", "DisplayName": "0x130", "EvaluateStateMachine": "0x1DD", "FOV": "0x160", "FakeDataModelPointer": "0x7d909f8",
      "FakeDataModelToDataModel": "0x1c0", "FogColor": "0xfc", "FogEnd": "0x134", "FogStart": "0x138", "ForceNewAFKDuration": "0x1F8",
      "FramePositionOffsetX": "0x4DC", "FramePositionOffsetY": "0x4E4", "FramePositionX": "0x4D8", "FramePositionY": "0x4E0", "FrameRotation": "0x188",
      "FrameSizeOffsetX": "0x500", "FrameSizeOffsetY": "0x504", "FrameSizeX": "0x538", "FrameSizeY": "0x4FC", "GameId": "0x190", "GameLoaded": "0x5f8",
      "Gravity": "0x1d0", "GravityContainer": "0x3d8", "Health": "0x194", "HealthDisplayDistance": "0x338", "HipHeight": "0x1a0", "HumanoidDisplayName": "0xD0",
      "HumanoidState": "0x8d8", "HumanoidStateId": "0x20", "InputObject": "0x100", "InsetMaxX": "0x100", "InsetMaxY": "0x104", "InsetMinX": "0xF8",
      "InsetMinY": "0xFC", "InstanceAttributePointer1": "0x48", "InstanceAttributePointer2": "0x18", "InstanceCapabilities": "0xD08",
      "JobEnd": "0x1d8", "JobId": "0x138", "JobStart": "0x1d0", "Job_Name": "0x18", "JobsPointer": "0x778C2C0", "JumpPower": "0x1b0",
      "LocalPlayer": "0x130", "LocalScriptByteCode": "0x1a8", "LocalScriptBytecodePointer": "0x10", "LocalScriptHash": "0x1b8", "MaterialType": "0x248",
      "MaxHealth": "0x1b4", "MaxSlopeAngle": "0x1b8", "MeshPartColor3": "0x194", "MeshPartTexture": "0x318", "ModelInstance": "0x380",
      "ModuleScriptByteCode": "0x150", "ModuleScriptBytecodePointer": "0x10", "ModuleScriptHash": "0x160", "MoonTextureId": "0xe0",
      "MousePosition": "0xec", "MouseSensitivity": "0x7e18770", "MoveDirection": "0x158", "Name": "0xb0", "NameDisplayDistance": "0x344",
      "NameSize": "0x10", "OnDemandInstance": "0x38", "OutdoorAmbient": "0x108", "Parent": "0x68", "PartSize": "0x1b0", "Ping": "0x2bc8",
      "PlaceId": "0x198", "PlayerConfigurerPointer": "0x7d6e028", "PlayerMouse": "0xd28", "Position": "0xe4", "Primitive": "0x148",
      "PrimitiveValidateValue": "0x6", "PrimitivesPointer1": "0x240", "PrimitivesPointer2": "0x240", "ProximityPromptActionText": "0xd0",
      "ProximityPromptEnabled": "0x156", "ProximityPromptGamepadKeyCode": "0x13c", "ProximityPromptHoldDuraction": "0x140",
      "ProximityPromptMaxActivationDistance": "0x148", "ProximityPromptMaxObjectText": "0xf0", "RenderJobToDataModel": "0x1B0",
      "RenderJobToFakeDataModel": "0x38", "RenderJobToRenderView": "0x218", "ReadOnlyGravity": "0xa28", "RequireBypass": "0x870", "RigType": "0x1c8",
      "Rotation": "0xc0", "RunContext": "0x148", "ScriptContext": "0x3f0", "Sit": "0x1DC", "SkyboxBk": "0x110", "SkyboxDn": "0x140",
      "SkyboxFt": "0x170", "SkyboxLf": "0x1a0", "SkyboxRt": "0x1d0", "SkyboxUp": "0x200", "SoundId": "0xE0", "StarCount": "0x260",
      "StringLength": "0x10", "SunTextureId": "0x230", "TagList": "0x0", "TaskSchedulerMaxFPS": "0x1b0", "TaskSchedulerPointer": "0x7e4ed08",
      "Team": "0x290", "TeamColor": "0xd0", "TextLabelText": "0xe08", "TextLabelVisible": "0x5b1", "Tool_Grip_Position": "0x454",
      "Transparency": "0xf0", "UserId": "0x2b8", "Value": "0xd0", "Velocity": "0xf0", "ViewportSize": "0x2ac", "VisualEngine": "0x10",
      "VisualEnginePointer": "0x79449e0", "VisualEngineToDataModel1": "0x700", "VisualEngineToDataModel2": "0x1c0", "WalkSpeed": "0x1d4",
      "WalkSpeedCheck": "0x3c0", "Workspace": "0x178", "WorkspaceToWorld": "0x3d8", "viewmatrix": "0x120"
    }
    """
updated_json_string = update_offsets_from_url(offsets_url, json_template)
offsets = None

offsets = json.loads(updated_json_string)

pid = None

while pid is None:
    pid = find_roblox_process()

    time.sleep(1) if pid is None else None

pm = MemoryManager(pid)
baseAddr = get_module_base(pid)

_CACHE_ERROR_SENTINEL = object()
_CACHE_ERROR_TIMEOUT  = 0.15

def cached_read(cache: dict, key: int, func, timeout=CACHE_TIMEOUT):
    now = time.time()
    if key in cache:
        val, ts = cache[key]
        age = now - ts
        if val is _CACHE_ERROR_SENTINEL:
            if age < _CACHE_ERROR_TIMEOUT:
                return None
        else:
            if age < timeout:
                return val
    try:
        val = func()
    except Exception:
        cache[key] = (_CACHE_ERROR_SENTINEL, now)
        return None
    cache[key] = (val, now)
    return val

def DRP(addr):
    if not addr or not is_valid_addr(addr):
        return 0
    try:
        result = cached_read(_cache["drp"], addr, lambda: pm.read_longlong(addr))
        return result if result is not None and is_valid_addr(result) else 0
    except Exception:
        return 0

def ReadRobloxString(addr: int) -> str:

    def read_str():
        try:
            length = pm.read_int(addr + 0x10)

            if length > 15:
                ptr = DRP(addr)

                return pm.read_string(ptr, length)

            else:

                    return pm.read_string(addr, length + 1)

        except Exception:
            return ""

    return cached_read(_cache["string"], addr, read_str)

def GetName(instance: int) -> str:
    if not instance or not is_valid_addr(instance):
        return ""
    result = cached_read(_cache["name"], instance, lambda: ReadRobloxString(DRP(instance + int(offsets['Name'], 16))))
    return result if isinstance(result, str) else ""

def GetChildren(instance: int) -> list:
    if not instance or not is_valid_addr(instance):
        return []
    now = time.time()
    if instance in _cache["children"]:
        children, ts = _cache["children"][instance]
        if now - ts < CACHE_TIMEOUT:
            return children.copy()
    start = DRP(instance + int(offsets['Children'], 16))
    if not start or not is_valid_addr(start):
        _cache["children"][instance] = ([], now)
        return []
    end = DRP(start + 8)
    if not is_valid_addr(end):
        _cache["children"][instance] = ([], now)
        return []
    children = []
    cur = DRP(start)
    _iters = 0
    while cur != end and len(children) < 2000 and _iters < 4000:
        _iters += 1
        if not is_valid_addr(cur):
            break
        try:
            child = pm.read_longlong(cur)
            if child and is_valid_addr(child):
                children.append(child)
        except Exception:
            break
        cur += 0x10
    _cache["children"][instance] = (children.copy(), now)
    return children
def calculate_hitchance(mouse_x, mouse_y, target_x, target_y, fov_radius):
    distance = math.sqrt((target_x - mouse_x) ** 2 + (target_y - mouse_y) ** 2)

    if distance > fov_radius:
        return 0.0

    distance_ratio = distance / fov_radius
    hitchance = (1.0 - distance_ratio) * 99.0 + 1.0

    return max(0.0, min(100.0, hitchance))

def FindFirstChildOfClass(instance: int, class_name: str) -> int:
    for child in GetChildren(instance):
        try:

            if GetClassName(child) == class_name:
                return child
        except:
            pass
    return 0

def LocalPlayer(players_instance: int) -> int:
    player, ts = _cache["localplayer"]
    now = time.time()
    if now - ts < CACHE_TIMEOUT and player != 0:
        return player
    try:
        lp = pm.read_longlong(players_instance + int(offsets["LocalPlayer"], 16))
        _cache["localplayer"] = (lp, now)
        return lp
    except:
        return 0

def GetCharacter(player: int) -> int:
    if not player:
        return 0

    try:
        current_game_id = pm.read_int(DataModel + int(offsets["GameId"], 16))
        if current_game_id == 1700503529:
            player_name = GetName(player)
            if not player_name or not Workspace:
                return 0

            for child in GetChildren(Workspace):
                try:
                    if GetClassName(child) == "Model":
                        model_name = GetName(child)
                        if model_name == player_name:
                            return child
                except:
                    continue
            return 0
    except:
        pass

    return cached_read(_cache["character"], player, lambda: pm.read_longlong(player + int(offsets["ModelInstance"], 16)))

def GetClassName(instance: int) -> str:
    if not instance or not is_valid_addr(instance):
        return ""
    def read_classname():
        ptr = pm.read_longlong(instance + 0x18)
        ptr = pm.read_longlong(ptr + 0x8)
        fl = pm.read_longlong(ptr + 0x18)
        if fl == 0x1F:
            ptr = pm.read_longlong(ptr)
        return ReadRobloxString(ptr)
    result = cached_read(_cache["class_name"], instance, read_classname)
    return result if isinstance(result, str) else ""

def GetHumanoidCached(char: int) -> int:
    if not char or not is_valid_addr(char):
        return 0
    result = cached_read(_cache["humanoid"], char, lambda: FindFirstChildOfClass(char, "Humanoid"))
    return result if result is not None and is_valid_addr(result) else 0

def GetMaxHealthCached(humanoid: int) -> float:
    if not humanoid or not is_valid_addr(humanoid):
        return 0.0
    def _read_max_hp():
        v = pm.read_float(humanoid + int(offsets["MaxHealth"], 16))
        return v if math.isfinite(float(v)) and v > 0 else 0.0
    return cached_read(_cache["maxhealth"], humanoid, _read_max_hp)

def GetPrimitive(instance: int) -> int:
    if not is_valid_addr(instance):
        return 0
    result = cached_read(_cache["primitive"], instance, lambda: pm.read_longlong(instance + int(offsets["Primitive"], 16)))
    return result if result is not None and is_valid_addr(result) else 0

def Position(instance: int) -> np.ndarray:
    prim = GetPrimitive(instance)
    if not is_valid_addr(prim):
        return np.zeros(3, dtype=np.float32)
    try:
        pos_offset = int(offsets["Position"], 16)
        result = np.frombuffer(pm.read_bytes(prim + pos_offset, 12), dtype=np.float32).copy()
        if not np.all(np.isfinite(result)):
            return np.zeros(3, dtype=np.float32)
        return result
    except Exception:
        return np.zeros(3, dtype=np.float32)

def batch_world_to_screen(positions: np.ndarray, view_matrix: np.ndarray, half_w: float, half_h: float):
    if positions.shape[0] == 0:
        return [None] * positions.shape[0]

    ones = np.ones((positions.shape[0], 1), dtype=np.float32)
    clip = np.hstack((positions, ones)) @ view_matrix.T
    w = clip[:, 3]

    valid = w > 0.001

    with np.errstate(divide='ignore', invalid='ignore'):
        ndc_x = np.where(valid, clip[:, 0] / w, 0)
        ndc_y = np.where(valid, clip[:, 1] / w, 0)

    in_frustum = valid & (np.abs(ndc_x) <= 1.05) & (np.abs(ndc_y) <= 1.05)

    screen_x = (ndc_x + 1) * half_w
    screen_y = (1 - ndc_y) * half_h

    screen_x_int = screen_x.astype(np.int32)
    screen_y_int = screen_y.astype(np.int32)

    result = []
    for i in range(len(positions)):
        if in_frustum[i]:
            result.append((int(screen_x_int[i]), int(screen_y_int[i])))
        else:
            result.append(None)
    return result

def GetCharacterData(char: int) -> dict:
    if not char:
        return {}

    now = time.time()
    cached = _cache["character_full"].get(char)

    if cached and now - cached["timestamp"] < CACHE_TIMEOUT:
        humanoid = cached.get("humanoid", 0)

        if humanoid == 0:
            _cache["character_full"].pop(char, None)
            return {}

        try:
            health = pm.read_float(humanoid + int(offsets["Health"], 16))
            if not math.isfinite(health) or health <= 0:
                cached["timestamp"] = now - CACHE_TIMEOUT
        except:
            _cache["character_full"].pop(char, None)
            return {}

        return cached

    children = GetChildren(char)
    if not children:
        _cache["character_full"].pop(char, None)
        return {}

    child_names = [GetName(c) for c in children]

    is_r15 = "UpperTorso" in child_names
    part_set = R15_PARTS if is_r15 else R6_PARTS

    parts = {}
    for inst, name in zip(children, child_names):
        if name in part_set and name != "HumanoidRootPart":
            parts[name] = inst

    humanoid = GetHumanoidCached(char)
    if humanoid == 0:
        _cache["character_full"].pop(char, None)
        return {}

    max_health = GetMaxHealthCached(humanoid)
    if max_health <= 0:
        _cache["character_full"].pop(char, None)
        return {}

    if not parts:
        _cache["character_full"].pop(char, None)
        return {}

    primitives = {}
    for name, part in parts.items():
        prim = GetPrimitive(part)
        if prim == 0:
            _cache["character_full"].pop(char, None)
            return {}
        primitives[name] = prim

        if char in _cache["character_full"]:
            old_prims = _cache["character_full"][char].get("primitives", {})
            old_prim = old_prims.get(name)
            if old_prim and old_prim != prim:
                _chams_geometry_cache.pop(old_prim, None)

    data = {
        "parts": parts,
        "primitives": primitives,
        "is_r15": is_r15,
        "humanoid": humanoid,
        "max_health": max_health,
        "timestamp": now
    }

    _cache["character_full"][char] = data
    return data

def GetTeamCached(player):
    if not player or player < 0x10000:
        return 0

    addr = player + int(offsets["Team"], 16)

    try:
        result = cached_read(
            _cache["team"],
            addr,
            lambda: pm.read_longlong(addr)
        )
        return result if result is not None else 0
    except Exception:
        return 0

def draw_line_outline(x1, y1, x2, y2, main_color, outline_color):

    offsets = [(1,0), (-1,0), (0,1), (0,-1)]
    for ox, oy in offsets:
        pme.draw_line(x1 + ox, y1 + oy, x2 + ox, y2 + oy, outline_color)

    pme.draw_line(x1, y1, x2, y2, main_color)

def smooth_lerp_dt(current, target, delta_time, speed=8.0):

    return current + (target - current) * (1 - math.exp(-speed * delta_time))
def draw_spinning_crosshair(cx, cy, size=12, gap=4, spin_angle=0.0, color=(255,255,255,255), outline=(0,0,0,255), lines=2):

    step = math.pi / lines

    for i in range(lines):
        angle = i * step + spin_angle
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)

        x1 = cx + cos_a * gap
        y1 = cy + sin_a * gap
        x2 = cx + cos_a * size
        y2 = cy + sin_a * size
        x3 = cx - cos_a * gap
        y3 = cy - sin_a * gap
        x4 = cx - cos_a * size
        y4 = cy - sin_a * size

        draw_line_outline(int(x1), int(y1), int(x2), int(y2), color, outline)
        draw_line_outline(int(x3), int(y3), int(x4), int(y4), color, outline)

def FindFirstChild(instance: int, name: str, recursive: bool = False) -> int:
    if not instance or not name:
        return 0

    for child in GetChildren(instance):
        try:
            if GetName(child) == name:
                return child
        except:
            continue

    if recursive:
        for child in GetChildren(instance):
            try:
                found = FindFirstChild(child, name, True)
                if found:
                    return found
            except:
                continue

    return 0

USER32 = ctypes.windll.user32

if ctypes.sizeof(ctypes.c_void_p) == 8:
    ULONG_PTR = ctypes.c_uint64
else:
    ULONG_PTR = ctypes.c_uint32

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",        wintypes.LONG),
        ("dy",        wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags",   wintypes.DWORD),
        ("time",      wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR)
    ]

class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTunion)
    ]

_accum_dx = 0.0
_accum_dy = 0.0

def send_input_move(dx: float, dy: float):

    global _accum_dx, _accum_dy

    _accum_dx += dx
    _accum_dy += dy

    int_dx = int(round(_accum_dx))
    int_dy = int(round(_accum_dy))

    _accum_dx -= int_dx
    _accum_dy -= int_dy

    if int_dx == 0 and int_dy == 0:
        return

    mi = MOUSEINPUT()
    mi.dx = int_dx
    mi.dy = int_dy
    mi.mouseData = 0
    mi.dwFlags = win32con.MOUSEEVENTF_MOVE
    mi.time = 0
    mi.dwExtraInfo = 0

    inp = INPUT()
    inp.type = 0
    inp.union.mi = mi

    USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
def project_world_to_screen(point3, view_matrix, half_w, half_h):

    vec = np.array([point3[0], point3[1], point3[2], 1.0], dtype=np.float32)
    clip = view_matrix.dot(vec)
    w = clip[3]
    if w == 0:
        return None
    ndc = clip[:3] / w
    screen_x = (ndc[0] + 1.0) * half_w
    screen_y = (1.0 - ndc[1]) * half_h

    return float(screen_x), float(screen_y)

enable_aimbot = False
enable_esp_box = False
box_mode = "Full"
enable_esp_skeleton = False
enable_health_bar = False
enable_fov_circle = False
enable_crosshair = False
team_check = False
distance_check_enabled = False
max_render_distance = 500
enable_text = False
fov_radius = 200
smoothing_factor = 100
fly_enabled = False
silent_hitchance = 75.0


fly_keybind = None
use_fov = True
use_smooth = True
fly_mode = "Hold"
enable_tool = False
fly_toggled = False
fly_active = False
fly_method = "Velocity"
fly_speed = 50
orbit_enabled = False
orbit_distance = 10.0
orbit_speed = 2.0
orbit_angle = 0.0


kill_all_enabled = False
kill_all_keybind = None
kill_all_trigger_mode = "Toggle"
kill_all_toggled = False
kill_all_target = None
kill_all_orbit_angle = 0.0
_last_kill_all_key_down = False

menu_keybind = 0x2D
menu_open = True
aim_mode = "Mouse"
chams_mode = "Filled"
legit_mode = False

hit_parts = ["Head", "Arms", "Torso", "Legs"]

esp_box_color = "#ffffff"
esp_box_outline_color = "#000000"
esp_skeleton_color = "#ffffff"
esp_skeleton_outline_color = "#000000"
health_bar_outline_color = "#000000"
crosshair_color = "#ffffff"
crosshair_outline_color = "#000000"
fov_circle_color = "#ffffff"
fov_circle_outline_color = "#000000"
tool_color = "#ffffff"
text_color = "#ffffff"
def hex_to_rgba(color, alpha=255):
    if isinstance(color, (list, tuple)):
        if all(isinstance(c, float) for c in color):
            r = int(color[0] * 255)
            g = int(color[1] * 255)
            b = int(color[2] * 255)
            a = alpha
            return r, g, b, a

        if len(color) == 4:
            return int(color[0]), int(color[1]), int(color[2]), alpha
        if len(color) == 3:
            return int(color[0]), int(color[1]), int(color[2]), alpha

    if isinstance(color, str):
        color = color.lstrip("#")
        r = int(color[0:2], 16)
        g = int(color[2:4], 16)
        b = int(color[4:6], 16)
        return r, g, b, alpha

    raise TypeError(f"Unsupported color format: {color}")
drag_pos = None

def mouse_drag_callback(sender, app_data):

    global drag_pos
    if drag_pos:
        new_pos = dpg.get_viewport_pos()
        delta = [app_data[1], app_data[2]]
        dpg.set_viewport_pos([new_pos[0] + delta[0], new_pos[1] + delta[1]])

def mouse_down_callback(sender, app_data):

    global drag_pos

    if app_data[0] == 0 and dpg.is_item_hovered("Primary Window"):
        drag_pos = True

def mouse_up_callback(sender, app_data):

    global drag_pos
    drag_pos = None
VK_CODES = {

    'Left Mouse': 0x01,
    'Right Mouse': 0x02,
    'Middle Mouse': 0x04,
    'X1 Mouse': 0x05,
    'X2 Mouse': 0x06,

    'Backspace': 0x08,
    'Tab': 0x09,
    'Enter': 0x0D,
    'Shift': 0x10,
    'Ctrl': 0x11,
    'Alt': 0x12,
    'Pause': 0x13,
    'Caps Lock': 0x14,
    'Esc': 0x1B,
    'Space': 0x20,

    'Page Up': 0x21,
    'Page Down': 0x22,
    'End': 0x23,
    'Home': 0x24,
    'Left Arrow': 0x25,
    'Up Arrow': 0x26,
    'Right Arrow': 0x27,
    'Down Arrow': 0x28,
    'Select': 0x29,
    'Print': 0x2A,
    'Execute': 0x2B,
    'Print Screen': 0x2C,
    'Insert': 0x2D,
    'Delete': 0x2E,
    'Help': 0x2F,

    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
    '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,

    'A': 0x41, 'B': 0x42, 'C': 0x43, 'D': 0x44, 'E': 0x45, 'F': 0x46,
    'G': 0x47, 'H': 0x48, 'I': 0x49, 'J': 0x4A, 'K': 0x4B, 'L': 0x4C,
    'M': 0x4D, 'N': 0x4E, 'O': 0x4F, 'P': 0x50, 'Q': 0x51, 'R': 0x52,
    'S': 0x53, 'T': 0x54, 'U': 0x55, 'V': 0x56, 'W': 0x57, 'X': 0x58,
    'Y': 0x59, 'Z': 0x5A,

    'Left Win': 0x5B,
    'Right Win': 0x5C,
    'Apps': 0x5D,

    'Numpad 0': 0x60,
    'Numpad 1': 0x61,
    'Numpad 2': 0x62,
    'Numpad 3': 0x63,
    'Numpad 4': 0x64,
    'Numpad 5': 0x65,
    'Numpad 6': 0x66,
    'Numpad 7': 0x67,
    'Numpad 8': 0x68,
    'Numpad 9': 0x69,
    'Multiply': 0x6A,
    'Add': 0x6B,
    'Separator': 0x6C,
    'Subtract': 0x6D,
    'Decimal': 0x6E,
    'Divide': 0x6F,

    'F1': 0x70, 'F2': 0x71, 'F3': 0x72, 'F4': 0x73, 'F5': 0x74,
    'F6': 0x75, 'F7': 0x76, 'F8': 0x77, 'F9': 0x78, 'F10': 0x79,
    'F11': 0x7A, 'F12': 0x7B, 'F13': 0x7C, 'F14': 0x7D, 'F15': 0x7E,
    'F16': 0x7F, 'F17': 0x80, 'F18': 0x81, 'F19': 0x82, 'F20': 0x83,
    'F21': 0x84, 'F22': 0x85, 'F23': 0x86, 'F24': 0x87,

    'Num Lock': 0x90,
    'Scroll Lock': 0x91,

    'Left Shift': 0xA0,
    'Right Shift': 0xA1,
    'Left Ctrl': 0xA2,
    'Right Ctrl': 0xA3,
    'Left Alt': 0xA4,
    'Right Alt': 0xA5,

    'Browser Back': 0xA6,
    'Browser Forward': 0xA7,
    'Browser Refresh': 0xA8,
    'Browser Stop': 0xA9,
    'Browser Search': 0xAA,
    'Browser Favorites': 0xAB,
    'Browser Home': 0xAC,

    'Volume Mute': 0xAD,
    'Volume Down': 0xAE,
    'Volume Up': 0xAF,
    'Next Track': 0xB0,
    'Previous Track': 0xB1,
    'Stop Media': 0xB2,
    'Play/Pause': 0xB3,
    'Mail': 0xB4,
    'Media Select': 0xB5,
    'Launch App 1': 0xB6,
    'Launch App 2': 0xB7,

    'Semicolon': 0xBA,
    'Equals': 0xBB,
    'Comma': 0xBC,
    'Minus': 0xBD,
    'Period': 0xBE,
    'Slash': 0xBF,
    'Grave': 0xC0,
    'Left Bracket': 0xDB,
    'Backslash': 0xDC,
    'Right Bracket': 0xDD,
    'Apostrophe': 0xDE,
}
def get_key_name(vk_code):
    if vk_code is None:
        return "None"
    for name, code in VK_CODES.items():

        if code == vk_code:

            return name

    return f"{vk_code}"
waiting_for_keybind = False
aimbot_keybind = None
def keybind_listener():
    global waiting_for_keybind, aimbot_keybind, fly_keybind, current_keybind_target, menu_keybind, kill_all_keybind

    while True:

        if waiting_for_keybind:
            time.sleep(0.3)

            for vk_code in range(1, 256):
                windll.user32.GetAsyncKeyState(vk_code)

            key_found = False
            while waiting_for_keybind and not key_found:
                for vk_code in range(1, 256):
                    if windll.user32.GetAsyncKeyState(vk_code) & 0x8000:
                        if vk_code == 27:
                            waiting_for_keybind = False
                            if current_keybind_target == "aimbot":
                                dpg.configure_item("keybind_button", label=f"{get_key_name(aimbot_keybind)}")
                            elif current_keybind_target == "fly":
                                dpg.configure_item("fly_keybind_button", label=f"{get_key_name(fly_keybind)}")
                            elif current_keybind_target == "menu":
                                dpg.configure_item("menu_keybind_button", label=f"{get_key_name(menu_keybind)}")
                            elif current_keybind_target == "kill_all":
                                dpg.configure_item("kill_all_keybind_button", label=f"{get_key_name(kill_all_keybind)}")

                            break

                        if current_keybind_target == "aimbot":
                            aimbot_keybind = vk_code
                            dpg.configure_item("keybind_button", label=f"{get_key_name(vk_code)}")
                        elif current_keybind_target == "fly":
                            fly_keybind = vk_code
                            dpg.configure_item("fly_keybind_button", label=f"{get_key_name(vk_code)}")
                        elif current_keybind_target == "menu":
                            menu_keybind = vk_code
                            dpg.configure_item("menu_keybind_button", label=f"{get_key_name(vk_code)}")
                        elif current_keybind_target == "kill_all":
                            kill_all_keybind = vk_code
                            dpg.configure_item("kill_all_keybind_button", label=f"{get_key_name(vk_code)}")

                        waiting_for_keybind = False
                        current_keybind_target = None
                        key_found = True
                        break

                time.sleep(0.01)
        else:
            time.sleep(0.1)

def keybind_callback():
    start_keybind_capture("aimbot")

threading.Thread(target=keybind_listener, daemon=True).start()

fake_ptr =None
DataModel = None
Players = None
Workspace = None

minimized = False
Cont = None
current_keybind_target = None

def start_keybind_capture(target):

    global waiting_for_keybind, current_keybind_target
    if not waiting_for_keybind:
        waiting_for_keybind = True
        current_keybind_target = target
        if target == "aimbot":
            dpg.configure_item("keybind_button", label="...")
        elif target == "fly":
            dpg.configure_item("fly_keybind_button", label="...")
        elif target == "menu":
             dpg.configure_item("menu_keybind_button", label="...")
        elif target == "kill_all":
            dpg.configure_item("kill_all_keybind_button", label="...")

def is_roblox_focused():
    hwnd = win32gui.GetForegroundWindow()
    window_title = win32gui.GetWindowText(hwnd)
    return window_title == "Roblox"
def is_menu_focused():
    hwnd = win32gui.GetForegroundWindow()
    window_title = win32gui.GetWindowText(hwnd)
    return window_title == "PhantomHook"


def is_key_pressed(vk_code):

    return windll.user32.GetAsyncKeyState(vk_code) & 0x8000


class orbThread(threading.Thread):
    def __init__(self, pm, base, offsets):
        super().__init__(daemon=True)
        self.pm = pm
        self.base = base
        self.offsets = offsets
        self.running = True
        self.zero_bytes = array([0.0, 0.0, 0.0], dtype=float32).tobytes()

        self.cache = {
            "hrp": (0, 0),
            "hrp_primitive": (0, 0),
            "workspace": (0, 0),
            "camera": (0, 0),
        }
        self.cache_timeout = 10

        self.last_position = None
        self.was_flying = False

    def cached_read(self, cache_key, func):
        now = time.time()
        if cache_key in self.cache:
            val, ts = self.cache[cache_key]
            if now - ts < self.cache_timeout and val != 0:
                return val

        val = func()
        self.cache[cache_key] = (val, now)
        return val

    def find_first_child(self, instance, name):
        if not instance:
            return 0
        try:
            start = self.pm.read_longlong(instance + int(self.offsets['Children'], 16))
            if not start:
                return 0
            end = self.pm.read_longlong(start + 8)
            cur = self.pm.read_longlong(start)

            max_iterations = 1000
            iteration = 0

            while cur != end and iteration < max_iterations:
                try:
                    child = self.pm.read_longlong(cur)
                    if child:
                        child_name = GetName(child)
                        if child_name == name:
                            return child
                except:
                    pass
                cur += 0x10
                iteration += 1
        except:
            pass
        return 0

    def get_humanoid_root_part(self, character):
        if not character:
            return 0

        cache_key = f"hrp_{character}"

        def find_hrp():
            return self.find_first_child(character, "HumanoidRootPart")

        return self.cached_read(cache_key, find_hrp)

    def get_hrp_primitive(self, hrp):
        if not hrp:
            return 0

        cache_key = f"hrp_primitive_{hrp}"

        def read_primitive():
            return self.pm.read_longlong(hrp + int(self.offsets['Primitive'], 16))

        return self.cached_read(cache_key, read_primitive)

    def get_workspace(self, datamodel):
        if not datamodel:
            return 0

        def read_workspace():
            return self.pm.read_longlong(datamodel + int(self.offsets['Workspace'], 16))

        return self.cached_read("workspace", read_workspace)

    def get_camera(self, workspace):
        if not workspace:
            return 0

        def read_camera():
            return self.pm.read_longlong(workspace + int(self.offsets['Camera'], 16))

        return self.cached_read("camera", read_camera)

    def run(self):
     try:
      global fly_enabled, fly_keybind, fly_mode, fly_toggled, fly_active, fly_speed
      global DataModel, Players
      global kill_all_enabled, kill_all_keybind, kill_all_trigger_mode
      global kill_all_toggled, kill_all_target, kill_all_orbit_angle
      global _last_kill_all_key_down

      last_key_state = False
      last_frame_time = time.perf_counter()

      while self.running:
          time.sleep(0.00000001)

          current_time = time.perf_counter()
          delta_time = current_time - last_frame_time
          last_frame_time = current_time
          delta_time = max(0.0001, min(delta_time, 0.1))

          # ── Kill All mode ──────────────────────────────────────────────────
          if kill_all_enabled:
              # Resolve keybind → active flag (hold or toggle)
              raw_ka_down = (windll.user32.GetAsyncKeyState(kill_all_keybind) & 0x8000) != 0 \
                            if kill_all_keybind is not None else False

              if kill_all_trigger_mode == "Toggle":
                  if raw_ka_down and not _last_kill_all_key_down:
                      kill_all_toggled = not kill_all_toggled
                  ka_active = kill_all_toggled
              else:
                  ka_active = raw_ka_down
                  if not ka_active:
                      kill_all_toggled = False
              _last_kill_all_key_down = raw_ka_down

              if ka_active:
                  try:
                      lp = LocalPlayer(Players)
                      if not lp:
                          time.sleep(0.01)
                          continue

                      ch = GetCharacter(lp)
                      if not ch:
                          time.sleep(0.01)
                          continue

                      hr = self.get_humanoid_root_part(ch)
                      pr = self.get_hrp_primitive(hr) if hr else 0
                      if not pr:
                          time.sleep(0.01)
                          continue

                      ws = self.get_workspace(DataModel)
                      ca = self.get_camera(ws) if ws else 0

                      # ── Auto-find nearest enemy ─────────────────────────
                      best_player = None
                      best_dist_sq = float('inf')
                      my_pos_bytes = self.pm.read_bytes(
                          pr + int(self.offsets['Position'], 16), 12)
                      my_pos = np.frombuffer(my_pos_bytes, dtype=float32).copy()

                      if is_valid_addr(Players):
                          for player in GetChildren(Players):
                              try:
                                  if player == lp:
                                      continue
                                  if team_check and GetTeamCached(player) == GetTeamCached(lp):
                                      continue
                                  tc = GetCharacter(player)
                                  if not tc:
                                      continue
                                  tcd = GetCharacterData(tc)
                                  if not tcd:
                                      continue
                                  humanoid = tcd.get("humanoid", 0)
                                  if not humanoid:
                                      continue
                                  hp = self.pm.read_float(
                                      humanoid + int(self.offsets['Health'], 16))
                                  if hp <= 0:
                                      continue
                                  thrp = self.get_humanoid_root_part(tc)
                                  tpr = self.get_hrp_primitive(thrp) if thrp else 0
                                  if not tpr:
                                      continue
                                  tpos = np.frombuffer(
                                      self.pm.read_bytes(
                                          tpr + int(self.offsets['Position'], 16), 12),
                                      dtype=float32)
                                  dx = tpos[0] - my_pos[0]
                                  dy = tpos[1] - my_pos[1]
                                  dz = tpos[2] - my_pos[2]
                                  dsq = dx*dx + dy*dy + dz*dz
                                  if dsq < best_dist_sq:
                                      best_dist_sq = dsq
                                      best_player = player
                                      kill_all_target = player
                              except Exception:
                                  continue

                      if best_player is None:
                          kill_all_target = None
                          time.sleep(0.02)
                          continue

                      # ── Orbit target ────────────────────────────────────
                      target_char = GetCharacter(best_player)
                      if not target_char:
                          time.sleep(0.01)
                          continue
                      thrp = self.get_humanoid_root_part(target_char)
                      tpr  = self.get_hrp_primitive(thrp) if thrp else 0
                      if not tpr:
                          time.sleep(0.01)
                          continue

                      tpos_bytes = self.pm.read_bytes(
                          tpr + int(self.offsets['Position'], 16), 12)
                      tpos = np.frombuffer(tpos_bytes, dtype=float32).copy()

                      kill_all_orbit_angle += orbit_speed * delta_time

                      new_x = tpos[0] + orbit_distance * math.cos(kill_all_orbit_angle)
                      new_z = tpos[2] + orbit_distance * math.sin(kill_all_orbit_angle)
                      new_y = tpos[1]

                      new_pos = np.array([new_x, new_y, new_z], dtype=float32)
                      pos_addr = pr + int(self.offsets['Position'], 16)
                      vel_addr = pr + int(self.offsets['Velocity'], 16)
                      self.pm.write_bytes(pos_addr, new_pos.tobytes(), 12)
                      self.pm.write_bytes(vel_addr, self.zero_bytes, 12)
                      self.pm.write_float(Cont + int(offsets["Gravity"], 16), 0.0)

                      # ── CFrame aimbot at target ──────────────────────────
                      if ca and is_valid_addr(ca):
                          try:
                              cam_rot_addr = ca + int(self.offsets['CameraRotation'], 16)
                              cam_pos_addr = ca + int(self.offsets['CameraPos'], 16)

                              # Camera sits at player pos; aim toward target head
                              # Use head if available, else HRP pos
                              tcd2 = GetCharacterData(target_char)
                              head_pos = tpos.copy()
                              if tcd2:
                                  parts = tcd2.get("parts", {})
                                  head_inst = parts.get("Head") or parts.get("UpperTorso")
                                  if head_inst:
                                      hp_prim = GetPrimitive(head_inst)
                                      if hp_prim and is_valid_addr(hp_prim):
                                          hp_bytes = self.pm.read_bytes(
                                              hp_prim + int(self.offsets['Position'], 16), 12)
                                          head_pos = np.frombuffer(hp_bytes, dtype=float32).copy()

                              # Direction from camera (=player pos) to head
                              dx = head_pos[0] - new_x
                              dy = head_pos[1] - new_y
                              dz = head_pos[2] - new_z
                              mag = math.sqrt(dx*dx + dy*dy + dz*dz)
                              if mag < 1e-6:
                                  continue
                              inv = 1.0 / mag
                              lx, ly, lz = dx*inv, dy*inv, dz*inv

                              # Build orthonormal basis for CFrame rotation matrix
                              if abs(ly) > 0.999:
                                  ux2, uy2, uz2 = 0.0, 0.0, -1.0
                              else:
                                  ux2, uy2, uz2 = 0.0, 1.0, 0.0

                              rx = uy2*lz - uz2*ly
                              ry = uz2*lx - ux2*lz
                              rz = ux2*ly - uy2*lx
                              mag_r = math.sqrt(rx*rx + ry*ry + rz*rz)
                              if mag_r < 1e-9:
                                  continue
                              inv_r = 1.0/mag_r
                              rx, ry, rz = rx*inv_r, ry*inv_r, rz*inv_r

                              ux2 = ly*rz - lz*ry
                              uy2 = lz*rx - lx*rz
                              uz2 = lx*ry - ly*rx

                              # Roblox CFrame rotation layout: [R U -L] column-major 3x3
                              rotation_data = np.array([
                                  -rx, ux2, -lx,
                                  -ry, uy2, -ly,
                                  -rz, uz2, -lz
                              ], dtype=float32)

                              if np.all(np.isfinite(rotation_data)):
                                  self.pm.write_bytes(cam_rot_addr, rotation_data.tobytes(), 36)

                              # Also write camera position = player pos
                              cam_pos_data = new_pos.tobytes()
                              self.pm.write_bytes(cam_pos_addr, cam_pos_data, 12)
                          except Exception as _aim_e:
                              pass

                  except Exception as _ka_e:
                      time.sleep(0.01)
                  continue  # skip normal orbit logic while kill_all is running
              else:
                  # Ka not active – restore gravity if orbit also inactive
                  kill_all_target = None
                  if not orbit_enabled:
                      try:
                          self.pm.write_float(Cont + int(offsets["Gravity"], 16), 196.1999969482422)
                      except Exception:
                          pass
                  time.sleep(0.01)
                  continue

          # ── Normal Orbit mode (original logic) ────────────────────────────
          if not orbit_enabled:
              time.sleep(0.1)
              continue

          if sticky_target is None:
              time.sleep(0.01)
              self.pm.write_float(Cont + int(offsets["Gravity"], 16), 196.1999969482422)
              continue

          try:
              lp = LocalPlayer(Players)
              if not lp:
                  continue

              ch = GetCharacter(lp)
              if not ch:
                  continue

              hr = self.get_humanoid_root_part(ch)
              if not hr:
                  self.cache.pop(f"hrp_{ch}", None)
                  continue

              pr = self.get_hrp_primitive(hr)
              if not pr:
                  self.cache.pop(f"hrp_primitive_{hr}", None)
                  continue

              ws = self.get_workspace(DataModel)
              if not ws:
                  self.cache.pop("workspace", None)
                  continue

              ca = self.get_camera(ws)
              if not ca:
                  self.cache.pop("camera", None)
                  continue

              cam_rot_addr = ca + int(self.offsets['CameraRotation'], 16)
              cam_matrix = []
              for i in range(9):
                  addr = cam_rot_addr + (i % 3) * 4 + (i // 3) * 12
                  cam_matrix.append(self.pm.read_float(addr))

              vel_addr = pr + int(self.offsets['Velocity'], 16)
              pos_addr = pr + int(self.offsets['Position'], 16)

              if orbit_enabled and sticky_target is not None:
                  try:
                      target_char = GetCharacter(sticky_target)
                      if target_char:
                          target_hrp = self.get_humanoid_root_part(target_char)
                          if target_hrp:
                              target_primitive = self.get_hrp_primitive(target_hrp)
                              if target_primitive:
                                  target_pos_addr = target_primitive + int(self.offsets['Position'], 16)
                                  target_pos_bytes = self.pm.read_bytes(target_pos_addr, 12)
                                  target_pos = np.frombuffer(target_pos_bytes, dtype=float32)

                                  global orbit_angle
                                  orbit_angle += orbit_speed * delta_time

                                  orbit_x = target_pos[0] + orbit_distance * math.cos(orbit_angle)
                                  orbit_z = target_pos[2] + orbit_distance * math.sin(orbit_angle)
                                  orbit_y = target_pos[1]

                                  orbit_pos = np.array([orbit_x, orbit_y, orbit_z], dtype=float32)

                                  self.pm.write_bytes(pos_addr, orbit_pos.tobytes(), 12)
                                  self.pm.write_bytes(vel_addr, self.zero_bytes, 12)
                                  self.pm.write_float(Cont + int(offsets["Gravity"], 16), 0.0)
                  except  Exception as e:
                      print(e)
                      pass

          except Exception as e:
              self.cache.clear()
              self.last_position = None
              time.sleep(0.01)
              pass
     except:
         pass

orb_thread = orbThread(pm, baseAddr, offsets)
orb_thread.start()
class FlyThread(threading.Thread):
    def __init__(self, pm, base, offsets):
        super().__init__(daemon=True)
        self.pm = pm
        self.base = base
        self.offsets = offsets
        self.running = True
        self.zero_bytes = array([0.0, 0.0, 0.0], dtype=float32).tobytes()

        self.cache = {
            "hrp": (0, 0),
            "hrp_primitive": (0, 0),
            "workspace": (0, 0),
            "camera": (0, 0),
        }
        self.cache_timeout = 10

        self.last_position = None
        self.was_flying = False

    def cached_read(self, cache_key, func):
        now = time.time()
        if cache_key in self.cache:
            val, ts = self.cache[cache_key]
            if now - ts < self.cache_timeout and val != 0:
                return val

        val = func()
        self.cache[cache_key] = (val, now)
        return val

    def find_first_child(self, instance, name):
        if not instance:
            return 0
        try:
            start = self.pm.read_longlong(instance + int(self.offsets['Children'], 16))
            if not start:
                return 0
            end = self.pm.read_longlong(start + 8)
            cur = self.pm.read_longlong(start)

            max_iterations = 1000
            iteration = 0

            while cur != end and iteration < max_iterations:
                try:
                    child = self.pm.read_longlong(cur)
                    if child:
                        child_name = GetName(child)
                        if child_name == name:
                            return child
                except:
                    pass
                cur += 0x10
                iteration += 1
        except:
            pass
        return 0

    def get_humanoid_root_part(self, character):
        if not character:
            return 0

        cache_key = f"hrp_{character}"

        def find_hrp():
            return self.find_first_child(character, "HumanoidRootPart")

        return self.cached_read(cache_key, find_hrp)

    def get_hrp_primitive(self, hrp):
        if not hrp:
            return 0

        cache_key = f"hrp_primitive_{hrp}"

        def read_primitive():
            return self.pm.read_longlong(hrp + int(self.offsets['Primitive'], 16))

        return self.cached_read(cache_key, read_primitive)

    def get_workspace(self, datamodel):
        if not datamodel:
            return 0

        def read_workspace():
            return self.pm.read_longlong(datamodel + int(self.offsets['Workspace'], 16))

        return self.cached_read("workspace", read_workspace)

    def get_camera(self, workspace):
        if not workspace:
            return 0

        def read_camera():
            return self.pm.read_longlong(workspace + int(self.offsets['Camera'], 16))

        return self.cached_read("camera", read_camera)

    def run(self):
     try:
      global fly_enabled, fly_keybind, fly_mode, fly_toggled, fly_active, fly_speed
      global DataModel, Players

      last_key_state = False
      last_frame_time = time.perf_counter()

      while self.running:
          time.sleep(0.00001)

          current_time = time.perf_counter()
          delta_time = current_time - last_frame_time
          last_frame_time = current_time

          if not fly_enabled:
              time.sleep(0.1)
              continue

          key_pressed = is_key_pressed(fly_keybind)

          if fly_mode == "Toggle":
              if key_pressed and not last_key_state:
                  fly_toggled = not fly_toggled
              should_fly = fly_toggled
          else:
              should_fly = key_pressed

          last_key_state = key_pressed

          if should_fly != self.was_flying:
              if not should_fly:
                  self.last_position = None
              self.was_flying = should_fly

          if not should_fly:
              time.sleep(0.01)
              self.pm.write_float(Cont + int(offsets["Gravity"], 16), 196.1999969482422)
              continue

          try:
              lp = LocalPlayer(Players)
              if not lp:
                  continue

              ch = GetCharacter(lp)
              if not ch:
                  continue

              hr = self.get_humanoid_root_part(ch)
              if not hr:
                  self.cache.pop(f"hrp_{ch}", None)
                  continue

              pr = self.get_hrp_primitive(hr)
              if not pr:
                  self.cache.pop(f"hrp_primitive_{hr}", None)
                  continue

              ws = self.get_workspace(DataModel)
              if not ws:
                  self.cache.pop("workspace", None)
                  continue

              ca = self.get_camera(ws)
              if not ca:
                  self.cache.pop("camera", None)
                  continue

              cam_rot_addr = ca + int(self.offsets['CameraRotation'], 16)
              cam_matrix = []
              for i in range(9):
                  addr = cam_rot_addr + (i % 3) * 4 + (i // 3) * 12
                  cam_matrix.append(self.pm.read_float(addr))

              look = array([-cam_matrix[2], -cam_matrix[5], -cam_matrix[8]], dtype=float32)
              right = array([cam_matrix[0], cam_matrix[3], cam_matrix[6]], dtype=float32)

              mv = array([0.0, 0.0, 0.0], dtype=float32)

              if windll.user32.GetAsyncKeyState(87) & 0x8000:
                  mv += look
              if windll.user32.GetAsyncKeyState(83) & 0x8000:
                  mv -= look
              if windll.user32.GetAsyncKeyState(65) & 0x8000:
                  mv -= right
              if windll.user32.GetAsyncKeyState(68) & 0x8000:
                  mv += right
              if windll.user32.GetAsyncKeyState(32) & 0x8000:
                  mv[1] += 1.0

              norm = linalg.norm(mv)
              vel_addr = pr + int(self.offsets['Velocity'], 16)
              pos_addr = pr + int(self.offsets['Position'], 16)

              if fly_method == "Velocity":
                  if norm > 0:
                      speed = fly_speed * 5.0
                      mv = (mv / norm) * speed
                      velocity_bytes = mv.tobytes()
                      self.pm.write_bytes(vel_addr, velocity_bytes, 12)
                  else:
                      self.pm.write_bytes(vel_addr, self.zero_bytes, 12)
                  self.pm.write_float(Cont + int(offsets["Gravity"], 16), 0.0)

              elif fly_method == "CFrame":
                  if norm > 0:

                      pos_bytes = self.pm.read_bytes(pos_addr, 12)
                      current_pos = np.frombuffer(pos_bytes, dtype=float32)

                      speed = fly_speed * delta_time * 5.0
                      mv = (mv / norm) * speed
                      new_pos = current_pos + mv

                      self.pm.write_bytes(pos_addr, new_pos.tobytes(), 12)

                      self.pm.write_bytes(vel_addr, self.zero_bytes, 12)
                  else:
                      self.pm.write_bytes(vel_addr, self.zero_bytes, 12)

                  self.pm.write_float(Cont + int(offsets["Gravity"], 16), 0.0)

          except Exception as e:
              self.cache.clear()
              self.last_position = None
              time.sleep(0.01)
              pass
     except:
         pass

fly_thread = FlyThread(pm, baseAddr, offsets)
fly_thread.start()
class MemoryAimbot:

    def __init__(self, pm, base, offsets):
        self.pm = pm
        self.base = base

        self.workspace_offset = int(offsets['Workspace'], 16)
        self.camera_offset = int(offsets['Camera'], 16)
        self.cam_rot_offset = int(offsets['CameraRotation'], 16)
        self.cam_pos_offset = int(offsets['CameraPos'], 16)

        self.cam_rot_addr = 0
        self.cam_pos_addr = 0
        self.last_datamodel = 0

        self.rotation_data = np.empty(9, dtype=np.float32)
        self.current_rotation = np.empty(9, dtype=np.float32)

    def update_addresses(self, datamodel):

        if datamodel == self.last_datamodel and self.cam_rot_addr:
            return True

        try:
            ws_addr = self.pm.read_longlong(datamodel + self.workspace_offset)
            if not ws_addr:
                return False

            cam_addr = self.pm.read_longlong(ws_addr + self.camera_offset)
            if not cam_addr:
                return False

            self.cam_rot_addr = cam_addr + self.cam_rot_offset
            self.cam_pos_addr = cam_addr + self.cam_pos_offset
            self.last_datamodel = datamodel

            return True
        except:
            return False

    def aim_at_target(self, target_3d_pos, datamodel, delta_time, smoothing):

        try:

            if not self.update_addresses(datamodel):
                return False

            cam_pos_bytes = self.pm.read_bytes(self.cam_pos_addr, 12)
            cam_pos = np.frombuffer(cam_pos_bytes, dtype=np.float32)

            current_rot_bytes = self.pm.read_bytes(self.cam_rot_addr, 36)
            np.copyto(self.current_rotation, np.frombuffer(current_rot_bytes, dtype=np.float32))

            dx = target_3d_pos[0] - cam_pos[0]
            dy = target_3d_pos[1] - cam_pos[1]
            dz = target_3d_pos[2] - cam_pos[2]

            mag = (dx*dx + dy*dy + dz*dz) ** 0.5
            if mag < 1e-6:
                return False

            inv_mag = 1.0 / mag
            lx, ly, lz = dx * inv_mag, dy * inv_mag, dz * inv_mag

            if abs(ly) > 0.999:
                ux, uy, uz = 0.0, 0.0, -1.0
            else:
                ux, uy, uz = 0.0, 1.0, 0.0

            rx = uy * lz - uz * ly
            ry = uz * lx - ux * lz
            rz = ux * ly - uy * lx

            mag_r = (rx*rx + ry*ry + rz*rz) ** 0.5
            inv_mag_r = 1.0 / mag_r
            rx, ry, rz = rx * inv_mag_r, ry * inv_mag_r, rz * inv_mag_r

            ux = ly * rz - lz * ry
            uy = lz * rx - lx * rz
            uz = lx * ry - ly * rx

            target_rot = np.array([
                -rx, ux, -lx,
                -ry, uy, -ly,
                -rz, uz, -lz
            ], dtype=np.float32)

            lerp_factor = 1.0 - math.exp(-smoothing * delta_time)

            for i in range(9):
                self.rotation_data[i] = self.current_rotation[i] + (target_rot[i] - self.current_rotation[i]) * lerp_factor

            self.pm.write_bytes(self.cam_rot_addr, self.rotation_data.tobytes(), 36)

            return True
        except Exception as e:

            self.cam_rot_addr = 0
            self.cam_pos_addr = 0
            self.last_datamodel = 0
            return False

def add_color_row(label, variable_name):
    current_color = globals().get(variable_name, [255, 255, 255, 255])

    with dpg.group(horizontal=True):
        dpg.add_text(label, color=(200, 200, 200))
        dpg.add_spacer(width=50)

    pass
def calculate_perfect_bounding_box(parts_dict, positions_np, name_to_idx, view_matrix, half_w, half_h, is_r15):
    all_positions = positions_np

    if len(all_positions) == 0:
        return None

    min_3d = np.min(all_positions, axis=0)
    max_3d = np.max(all_positions, axis=0)

    corners_3d = np.array([
        [min_3d[0], min_3d[1], min_3d[2]],
        [max_3d[0], min_3d[1], min_3d[2]],
        [min_3d[0], max_3d[1], min_3d[2]],
        [max_3d[0], max_3d[1], min_3d[2]],
        [min_3d[0], min_3d[1], max_3d[2]],
        [max_3d[0], min_3d[1], max_3d[2]],
        [min_3d[0], max_3d[1], max_3d[2]],
        [max_3d[0], max_3d[1], max_3d[2]],
    ], dtype=np.float32)

    screen_corners = batch_world_to_screen(corners_3d, view_matrix, half_w, half_h)

    valid_corners = [pt for pt in screen_corners if pt is not None]

    if not valid_corners:
        return None

    xs, ys = zip(*valid_corners)
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    padding = 4
    box_x = min_x - padding
    box_y = min_y - padding
    box_w = (max_x - min_x) + padding * 2
    box_h = (max_y - min_y) + padding * 2

    return {
        'x': box_x,
        'y': box_y,
        'w': box_w,
        'h': box_h
    }

def is_point_inside_bbox(point_x, point_y, bbox):
    if bbox is None:
        return False

    box_x = bbox['x']
    box_y = bbox['y']
    box_w = bbox['w']
    box_h = bbox['h']

    return (box_x <= point_x <= box_x + box_w and
            box_y <= point_y <= box_y + box_h)

enable_chams = False
chams_color = "#ffffff"

chams_transparency = 255

_chams_geometry_cache = {}

_hrp_position_cache = {}

def get_hrp_position_cached(character_addr):
    now = time.perf_counter()

    cached = _hrp_position_cache.get(character_addr)
    if cached and (now - cached['time']) < 0.016:
        return cached['position']

    try:
        hrp = FindFirstChild(character_addr, "HumanoidRootPart")
        if hrp:
            hrp_pos = Position(hrp)
            _hrp_position_cache[character_addr] = {
                'position': hrp_pos,
                'time': now
            }
            return hrp_pos
    except:
        pass

    return None

def get_chams_geometry(primitive_addr):
    now = time.perf_counter()

    cached = _chams_geometry_cache.get(primitive_addr)
    if cached:
        if now - cached['time'] < 1.0:
            return cached['data']

    if not is_valid_addr(primitive_addr):
        _chams_geometry_cache[primitive_addr] = {'data': None, 'time': now}
        return None

    try:
        size_offset = int(offsets["PartSize"], 16)
        size_bytes = pm.read_bytes(primitive_addr + size_offset, 12)
        size = np.frombuffer(size_bytes, dtype=np.float32).copy()

        if not np.all(np.isfinite(size)) or np.any(size <= 0) or np.any(size > 2048):
            _chams_geometry_cache[primitive_addr] = {'data': None, 'time': now}
            return None

        half_size = size * 0.5

        local_corners = np.array([
            [-half_size[0], -half_size[1], -half_size[2]],
            [ half_size[0], -half_size[1], -half_size[2]],
            [ half_size[0],  half_size[1], -half_size[2]],
            [-half_size[0],  half_size[1], -half_size[2]],
            [-half_size[0], -half_size[1],  half_size[2]],
            [ half_size[0], -half_size[1],  half_size[2]],
            [ half_size[0],  half_size[1],  half_size[2]],
            [-half_size[0],  half_size[1],  half_size[2]],
        ], dtype=np.float32)

        data = {"size": size, "local_corners": local_corners}
        _chams_geometry_cache[primitive_addr] = {'data': data, 'time': now}
        return data
    except Exception:
        _chams_geometry_cache[primitive_addr] = {'data': None, 'time': now}
        return None

def get_chams_transform(primitive_addr):
    now = time.perf_counter()

    cached = _chams_transform_cache.get(primitive_addr)
    if cached:
        if now - cached['time'] < 0.016:
            return cached['data']

    if not is_valid_addr(primitive_addr):
        _chams_transform_cache[primitive_addr] = {'data': None, 'time': now}
        return None

    try:
        pos_offset = int(offsets["Position"], 16)
        rot_offset = int(offsets["Rotation"], 16)

        if rot_offset == pos_offset + 12:
            combined_bytes = pm.read_bytes(primitive_addr + pos_offset, 48)
            position = np.frombuffer(combined_bytes[:12], dtype=np.float32).copy()
            rotation = np.frombuffer(combined_bytes[12:], dtype=np.float32).reshape(3, 3).copy()
        else:
            pos_bytes = pm.read_bytes(primitive_addr + pos_offset, 12)
            position = np.frombuffer(pos_bytes, dtype=np.float32).copy()
            rot_bytes = pm.read_bytes(primitive_addr + rot_offset, 36)
            rotation = np.frombuffer(rot_bytes, dtype=np.float32).reshape(3, 3).copy()

        if not np.all(np.isfinite(position)) or not np.all(np.isfinite(rotation)):
            _chams_transform_cache[primitive_addr] = {'data': None, 'time': now}
            return None

        data = {'position': position, 'rotation': rotation}
        _chams_transform_cache[primitive_addr] = {'data': data, 'time': now}
        return data
    except Exception:
        _chams_transform_cache[primitive_addr] = {'data': None, 'time': now}
        return None

_chams_transform_cache = {}

CHAMS_FACES = (
    (4, 5, 6, 7),
    (1, 0, 3, 2),
    (0, 4, 7, 3),
    (5, 1, 2, 6),
    (7, 6, 2, 3),
    (0, 1, 5, 4),
)
CHAMS_EDGES = (
    (0, 1), (1, 2), (2, 3), (3, 0),
    (4, 5), (5, 6), (6, 7), (7, 4),
    (0, 4), (1, 5), (2, 6), (3, 7),
)


class AimbotThread(threading.Thread):
    def __init__(self, pm, base, offsets):
        super().__init__(daemon=True)
        self.pm = pm
        self.base = base
        self.offsets = offsets

        self.memory_aimbot = MemoryAimbot(pm, base, offsets)

        self.players = []
        self.players_lock = threading.Lock()

        self.running = True
        self.update_interval = 0.12
        self.aim_interval = 0.008

        self._get_children = GetChildren
        self._localplayer_fn = LocalPlayer
        self._get_char = GetCharacter
        self._get_char_data = GetCharacterData
        self._get_team = GetTeamCached
        self._pos = Position
        self._project = project_world_to_screen

        self.visual_ptr = None
        self.viewmatrix_addr = None

    def stop(self):
        self.running = False

    def refresh_visual_engine(self):
        try:
            visualEngine = self.pm.read_longlong(self.base + int(self.offsets['VisualEnginePointer'], 16))
            if visualEngine:
                self.visual_ptr = visualEngine
                self.viewmatrix_addr = visualEngine + int(self.offsets['viewmatrix'], 16)
        except:
            self.visual_ptr = None
            self.viewmatrix_addr = None

    def update_player_list(self):
        global Players
        try:
            Pl = Players
            if not Pl:
                return
            raw = self._get_children(Pl)
            with self.players_lock:
                self.players = [p for p in raw if p]
        except Exception:
            pass

    def find_best_target(self, use_fov, fov_radius, local_player, camera_pos, half_w, half_h, view_matrix):
        best = (None, None, None, None, float('inf'))
        with self.players_lock:
            players_snapshot = list(self.players)

        for player in players_snapshot:
            try:
                if player == local_player:
                    continue

                char = self._get_char(player)
                if not char:
                    continue

                if team_check and self._get_team(player) == GetTeamCached(local_player):
                    continue

                char_data = self._get_char_data(char)
                if not char_data:
                    continue

                humanoid = char_data.get("humanoid", 0)
                if not humanoid:
                    continue
                health = pm.read_float(humanoid + int(offsets["Health"], 16))
                if health <= 0 or char_data.get("max_health", 0) <= 0:
                    continue

                parts_dict = char_data["parts"]
                if "Head" not in parts_dict:
                    continue

                head_prim = parts_dict["Head"]
                head_world = self._pos(head_prim)

                if view_matrix is not None and half_w is not None and half_h is not None:
                    scr = self._project(head_world, view_matrix, half_w, half_h)
                else:
                    scr = None

                if scr is not None:
                    cx = scr[0]; cy = scr[1]
                    dx = cx - half_w
                    dy = cy - half_h
                    dist = dx*dx + dy*dy
                else:
                    dist = float('inf')

                if use_fov and scr is not None:
                    if math.sqrt(dist) > fov_radius:
                        continue

                if dist < best[4]:
                    best = (player, char, head_world, scr, dist)

            except Exception:
                continue

        return best if best[0] is not None else (None, None, None, None)

    def do_mouse_aim(self, target_screen, delta_time, smoothing):
        if target_screen is None:
            return False
        try:
            tx, ty = float(target_screen[0]), float(target_screen[1])
            cx, cy = win32api.GetCursorPos()
            dx = tx - cx
            dy = ty - cy
            s = max(0.001, min(100.0, smoothing))
            lerp_t = 1.0 - math.exp(-s * delta_time)
            move_x = dx * lerp_t
            move_y = dy * lerp_t
            send_input_move(move_x, move_y)
            return True
        except:
            return False

    def run(self):
        last_update = 0.0
        last_aim = 0.0
        last_time = time.perf_counter()

        self.refresh_visual_engine()

        while self.running:
            now = time.perf_counter()
            dt = now - last_time
            last_time = now

            if now - last_update >= self.update_interval:
                self.update_player_list()
                last_update = now

            if now - last_aim < self.aim_interval:
                time.sleep(0.001)
                continue
            last_aim = now

            if not enable_aimbot:
                time.sleep(0.02)
                continue

            if not Players:
                time.sleep(0.05)
                continue

            local_player = LocalPlayer(Players)
            if not local_player:
                time.sleep(0.05)
                continue

            if not (win32api.GetKeyState(aimbot_keybind) < 0) and not (aimbot_trigger_mode == "Toggle" and aimbot_toggled):
                time.sleep(0.01)
                continue

            view_matrix = None
            half_w = half_h = None
            try:
                if not self.viewmatrix_addr:
                    self.refresh_visual_engine()
                if self.viewmatrix_addr:
                    raw = self.pm.read_bytes(self.viewmatrix_addr, 64)
                    vm = np.frombuffer(raw, dtype=np.float32)
                    if vm.size >= 16:
                        view_matrix = vm.reshape((4,4))
                    win_x, win_y, win_width, win_height = get_roblox_window_rect()
                    half_w = win_width * 0.5
                    half_h = win_height * 0.5
            except:
                view_matrix = None

            player, char, head_world, screen_xy = self.find_best_target(use_fov, fov_radius, local_player,
                                                                         None, half_w, half_h, view_matrix)

            if not player:
                time.sleep(0.01)
                continue

            if aim_mode == "Memory":
                datamodel = DataModel
                smoothing_val = (smoothing_factor / 100.0) * 8.0 if use_smooth else 999.0
                self.memory_aimbot.aim_at_target(head_world, datamodel, dt, smoothing_val)
            else:
                target_screen = screen_xy
                if target_screen is None:
                    if self.viewmatrix_addr:
                        try:
                            raw = self.pm.read_bytes(self.viewmatrix_addr, 64)
                            vm = np.frombuffer(raw, dtype=np.float32)
                            if vm.size >= 16:
                                view_matrix = vm.reshape((4,4))
                                win_x, win_y, win_width, win_height = get_roblox_window_rect()
                                half_w = win_width * 0.5; half_h = win_height * 0.5
                                target_screen = self._project(head_world, view_matrix, half_w, half_h)
                        except:
                            target_screen = None
                if target_screen is not None:
                    self.do_mouse_aim(target_screen, dt, smoothing_factor if use_smooth else 999.0)

            time.sleep(0.0005)

def Render():
   global fake_ptr,DataModel,Players,Workspace,minimized,Cont,dpg_hwnd
   global sticky_target, sticky_target_id, last_aimbot_state

   def switch_tab(tab_name):
    tabs = ["aimbot", "visuals", "misc", "movement", "config"]
    for t in tabs:
        if dpg.does_item_exist(f"content_{t}"):
            dpg.configure_item(f"content_{t}", show=False)
        if dpg.does_item_exist(f"config_{t}"):
            dpg.configure_item(f"config_{t}", show=False)

        if dpg.does_item_exist(f"tab_{t}"):
            dpg.configure_item(f"tab_{t}", label=t)

    if dpg.does_item_exist(f"content_{tab_name}"):
        dpg.configure_item(f"content_{tab_name}", show=True)
    if dpg.does_item_exist(f"config_{tab_name}"):
        dpg.configure_item(f"config_{tab_name}", show=True)

    if dpg.does_item_exist(f"tab_{tab_name}"):
        dpg.bind_item_theme(f"tab_{tab_name}", "selected_tab_theme")

    for t in tabs:
        if t != tab_name and dpg.does_item_exist(f"tab_{t}"):
            dpg.bind_item_theme(f"tab_{t}", "small_tab_theme")

   def create_toggle_button(label, tag, variable_name, default_value):
       def toggle_callback(sender):
           current_value = globals()[variable_name]
           new_value = not current_value
           globals()[variable_name] = new_value
           if new_value:
               dpg.bind_item_theme(tag, "toggle_button_on")
           else:
               dpg.bind_item_theme(tag, "toggle_button_off")

       with dpg.group(horizontal=True):
           dpg.add_button(label="", tag=tag, callback=toggle_callback, width=10, height=10)
           dpg.add_spacer(width=2)
           dpg.add_text(label, color=(200, 200, 200))

       if default_value:
           dpg.bind_item_theme(tag, "toggle_button_on")
       else:
           dpg.bind_item_theme(tag, "toggle_button_off")

   def render_settings_window():
    global minimized

    if dpg.does_item_exist("Primary Window"):
        return

    width, height = (560, 455)
    dpg.set_viewport_width(width)
    dpg.set_viewport_height(height)

    with dpg.window(label="PhantomHook", width=width, height=height,
                    no_title_bar=True, no_resize=True,
                    tag="Primary Window", no_scrollbar=False,
                    horizontal_scrollbar=False):

        with dpg.group(horizontal=True):
            dpg.add_text("Phantom", color=(255,255,255))
            dpg.add_text("Hook", color=(100, 150, 220))
            dpg.add_spacer(width=80)

            dpg.add_button(label="aimbot", width=60, height=18,
                        callback=lambda: switch_tab("aimbot"),
                        tag="tab_aimbot")
            dpg.bind_item_theme("tab_aimbot", "selected_tab_theme")

            dpg.add_button(label="visuals", width=70, height=18,
                        callback=lambda: switch_tab("visuals"),
                        tag="tab_visuals")
            dpg.bind_item_theme("tab_visuals", "small_tab_theme")

            dpg.add_button(label="misc", width=50, height=18,
                        callback=lambda: switch_tab("misc"),
                        tag="tab_misc")
            dpg.bind_item_theme("tab_misc", "small_tab_theme")

            dpg.add_button(label="movement", width=75, height=18,
                        callback=lambda: switch_tab("movement"),
                        tag="tab_movement")
            dpg.bind_item_theme("tab_movement", "small_tab_theme")

            dpg.add_button(label="config", width=62, height=18,
                        callback=lambda: switch_tab("config"),
                        tag="tab_config")
            dpg.bind_item_theme("tab_config", "small_tab_theme")

        dpg.add_separator()

        with dpg.group(horizontal=True):
            with dpg.child_window(width=250, height=400, tag="left_panel", border=True):

                with dpg.group(tag="content_aimbot", show=True):
                    dpg.add_separator()
                    dpg.add_spacer(height=5)
                    create_toggle_button("Enabled", "aimbot_checkbox", "enable_aimbot", enable_aimbot)
                    create_toggle_button("Use FOV", "use_fov_checkbox", "use_fov", use_fov)
                    create_toggle_button("Use Smooth", "use_smooth_checkbox", "use_smooth", use_smooth)
                    create_toggle_button("Humanization", "legit_mode_checkbox", "legit_mode", legit_mode)
                    create_toggle_button("Sticky Aim", "sticky_aim_checkbox", "sticky_aim_enabled", sticky_aim_enabled)
                    dpg.add_slider_float(
                        label="HitChance",
                        default_value=75.0,
                        min_value=0.0,
                        max_value=100.0,
                        format="%.0f",
                        callback=lambda s, a: globals().update({'silent_hitchance': a}),
                        tag="silent_hitchance_slider"
                    )
                    dpg.add_spacer(height=2)
                    dpg.bind_item_theme("silent_hitchance_slider", "thin_slider_theme")

                    def toggle_hit_part(sender):
                        global hit_parts
                        part = dpg.get_item_user_data(sender)
                        if part in hit_parts:
                            hit_parts.remove(part)
                            dpg.bind_item_theme(sender, "toggle_button_off")
                        else:
                            hit_parts.append(part)
                            dpg.bind_item_theme(sender, "toggle_button_on")

                    with dpg.group(horizontal=True):
                        dpg.add_button(label="", tag="hitpart_head", callback=toggle_hit_part, user_data="Head", width=10, height=10)
                        dpg.add_spacer(width=2)
                        dpg.add_text("Head", color=(200, 200, 200))
                    dpg.set_item_user_data("hitpart_head", "Head")
                    if "Head" in hit_parts:
                        dpg.bind_item_theme("hitpart_head", "toggle_button_on")
                    else:
                        dpg.bind_item_theme("hitpart_head", "toggle_button_off")

                    with dpg.group(horizontal=True):
                        dpg.add_button(label="", tag="hitpart_arms", callback=toggle_hit_part, user_data="Arms", width=10, height=10)
                        dpg.add_spacer(width=2)
                        dpg.add_text("Arms", color=(200, 200, 200))
                    dpg.set_item_user_data("hitpart_arms", "Arms")
                    if "Arms" in hit_parts:
                        dpg.bind_item_theme("hitpart_arms", "toggle_button_on")
                    else:
                        dpg.bind_item_theme("hitpart_arms", "toggle_button_off")

                    with dpg.group(horizontal=True):
                        dpg.add_button(label="", tag="hitpart_torso", callback=toggle_hit_part, user_data="Torso", width=10, height=10)
                        dpg.add_spacer(width=2)
                        dpg.add_text("Torso", color=(200, 200, 200))
                    dpg.set_item_user_data("hitpart_torso", "Torso")
                    if "Torso" in hit_parts:
                        dpg.bind_item_theme("hitpart_torso", "toggle_button_on")
                    else:
                        dpg.bind_item_theme("hitpart_torso", "toggle_button_off")

                    with dpg.group(horizontal=True):
                        dpg.add_button(label="", tag="hitpart_legs", callback=toggle_hit_part, user_data="Legs", width=10, height=10)
                        dpg.add_spacer(width=2)
                        dpg.add_text("Legs", color=(200, 200, 200))
                    dpg.set_item_user_data("hitpart_legs", "Legs")
                    if "Legs" in hit_parts:
                        dpg.bind_item_theme("hitpart_legs", "toggle_button_on")
                    else:
                        dpg.bind_item_theme("hitpart_legs", "toggle_button_off")

                    dpg.add_separator()

                    dpg.add_combo(label="Aim Method", items=["Mouse", "Memory","Silent"],
                                default_value=aim_mode, tag="aim_mode_combo", width=100,
                                callback=lambda s, a: globals().__setitem__('aim_mode', a))

                    dpg.add_combo(label="Aim Mode", items=["Hold", "Toggle"],
                                default_value=aimbot_trigger_mode, tag="aim_trigger_combo", width=100,
                                callback=lambda s, a: globals().__setitem__('aimbot_trigger_mode', a))

                    with dpg.group(horizontal=True):

                        dpg.add_button(label=f"{get_key_name(aimbot_keybind)}",
                                     tag="keybind_button", callback=keybind_callback, width=105, height=20)
                        dpg.bind_item_theme("keybind_button", "keybind_button_theme")

                with dpg.group(tag="content_visuals", show=False):
                    dpg.add_separator()
                    dpg.add_spacer(height=5)

                    create_toggle_button("Box", "box_checkbox", "enable_esp_box", enable_esp_box)
                    dpg.add_combo(
                        items=["Full", "Corners"],
                        default_value=box_mode,
                        label="Box Mode",
                        tag="box_mode_combo",
                        width=100,
                        callback=lambda s, a: globals().__setitem__('box_mode', a),
                    )
                    dpg.add_spacer(height=2)
                    create_toggle_button("Health Bar", "health_checkbox", "enable_health_bar", enable_health_bar)
                    create_toggle_button("Skeleton", "skeleton_checkbox", "enable_esp_skeleton", enable_esp_skeleton)
                    create_toggle_button("Chams", "chams_checkbox", "enable_chams", enable_chams)
                    dpg.add_combo(
                        items=["Filled", "Outline"],
                        default_value=chams_mode,
                        label="Chams Mode",
                        tag="chams_mode_combo",
                        width=100,
                        callback=lambda s, a:globals().__setitem__('chams_mode', a),

                    )
                    dpg.add_spacer(height=2)

                    create_toggle_button("Name", "name_checkbox", "enable_text", enable_text)
                    create_toggle_button("Tool", "tool_checkbox", "enable_tool", enable_tool)
                    create_toggle_button("FOV Circle", "fov_circle_checkbox", "enable_fov_circle", enable_fov_circle)
                    create_toggle_button("Crosshair", "crosshair_checkbox", "enable_crosshair", enable_crosshair)

                with dpg.group(tag="content_misc", show=False):
                    dpg.add_separator()
                    dpg.add_spacer(height=5)
                    create_toggle_button("Team Check", "team_checkbox", "team_check", team_check)
                    create_toggle_button("Distance Check", "distance_check_checkbox", "distance_check_enabled", distance_check_enabled)
                    dpg.add_slider_int(
                        label="Max Distance",
                        default_value=max_render_distance,
                        min_value=1,
                        max_value=1000,
                        format="%d",
                        callback=lambda s, a: globals().update({'max_render_distance': a}),
                        tag="max_distance_slider",
                        width=200
                    )
                    dpg.add_spacer(height=2)
                    dpg.bind_item_theme("max_distance_slider", "thin_slider_theme")

                    def toggle_hide_console(sender):
                        current = globals()["hide_console"]
                        new_value = not current
                        globals()["hide_console"] = new_value
                        toggle_console(new_value)
                        if new_value:
                            dpg.bind_item_theme(sender, "toggle_button_on")
                        else:
                            dpg.bind_item_theme(sender, "toggle_button_off")

                    with dpg.group(horizontal=True):
                        dpg.add_button(label="", tag="hide_console_checkbox", callback=toggle_hide_console, width=10, height=10)
                        dpg.add_spacer(width=2)
                        dpg.add_text("Hide Console", color=(200, 200, 200))
                    if hide_console:
                        dpg.bind_item_theme("hide_console_checkbox", "toggle_button_on")
                    else:
                        dpg.bind_item_theme("hide_console_checkbox", "toggle_button_off")

                    def toggle_streamproof_button(sender):
                        current = globals()["streamproof"]
                        new_value = not current
                        toggle_streamproof(new_value)
                        if globals()["streamproof"]:
                            dpg.bind_item_theme(sender, "toggle_button_on")
                        else:
                            dpg.bind_item_theme(sender, "toggle_button_off")

                    with dpg.group(horizontal=True):
                        dpg.add_button(label="", tag="streamproof_checkbox", callback=toggle_streamproof_button, width=10, height=10)
                        dpg.add_spacer(width=2)
                        dpg.add_text("StreamProof", color=(200, 200, 200))
                    if streamproof:
                        dpg.bind_item_theme("streamproof_checkbox", "toggle_button_on")
                    else:
                        dpg.bind_item_theme("streamproof_checkbox", "toggle_button_off")

                with dpg.group(tag="content_movement", show=False):
                    dpg.add_separator()
                    dpg.add_spacer(height=5)
                    create_toggle_button("Fly", "fly_checkbox", "fly_enabled", fly_enabled)

                    dpg.add_combo(label="Fly Mode", items=["Hold", "Toggle"],
                                default_value=fly_mode, tag="fly_mode_combo", width=100,
                                callback=lambda s, a: globals().__setitem__('fly_mode', a))
                    dpg.add_combo(label="Fly Method", items=["Velocity", "CFrame"],
                                default_value=fly_method, tag="fly_method_combo", width=100,
                                callback=lambda s, a: globals().__setitem__('fly_method', a))

                    with dpg.group(horizontal=True):

                        dpg.add_button(label=f"{get_key_name(fly_keybind)}",
                                     tag="fly_keybind_button",
                                     callback=lambda: start_keybind_capture("fly"), width=105, height=20)
                        dpg.bind_item_theme("fly_keybind_button", "keybind_button_theme")

                    dpg.add_spacer(height=10)
                    create_toggle_button("Orbit", "orbit_checkbox", "orbit_enabled", orbit_enabled)

                    dpg.add_separator()
                    dpg.add_spacer(height=5)

                    # ── Kill All ───────────────────────────────────────────
                    create_toggle_button("Kill All", "kill_all_checkbox", "kill_all_enabled", kill_all_enabled)

                    with dpg.group(horizontal=True):
                        dpg.add_button(label=f"{get_key_name(kill_all_keybind)}",
                                       tag="kill_all_keybind_button",
                                       callback=lambda: start_keybind_capture("kill_all"),
                                       width=105, height=20)
                        dpg.bind_item_theme("kill_all_keybind_button", "keybind_button_theme")

                    dpg.add_combo(label="KA Mode", items=["Hold", "Toggle"],
                                  default_value=kill_all_trigger_mode,
                                  tag="kill_all_trigger_combo", width=100,
                                  callback=lambda s, a: globals().__setitem__('kill_all_trigger_mode', a))

                with dpg.group(tag="content_config", show=False):
                    dpg.add_separator()
                    dpg.add_input_text(label="", tag="config_name_input",
                                     hint="Enter config name...", width=230)
                    dpg.add_button(label="Save Config", callback=save_config_callback, width=230)

                    dpg.add_listbox(tag="config_list", items=get_config_list(),
                                  num_items=8, width=230)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Load", callback=load_config_callback, width=110)
                        dpg.add_button(label="Delete", callback=delete_config_callback, width=110)
                    dpg.add_button(label="Open Folder", callback=lambda: open_config_folder(), width=230)

            with dpg.child_window(width=280, height=400, tag="right_panel", border=True):

                with dpg.group(tag="config_aimbot", show=True):
                    dpg.add_separator()

                    with dpg.group(horizontal=True):
                        dpg.add_text("FOV Radius", color=(200, 200, 200))
                    dpg.add_slider_int(label="##fov", default_value=fov_radius // 10,
                                     min_value=1, max_value=100, tag="fov_slider", width=250,
                                     format="%d", clamped=True,
                                     callback=lambda s, a: globals().__setitem__('fov_radius', a * 10))
                    dpg.bind_item_theme("fov_slider", "thin_slider_theme")

                    with dpg.group(horizontal=True):
                        dpg.add_text("Smoothing", color=(200, 200, 200))
                    dpg.add_slider_int(label="##smooth", default_value=smoothing_factor // 10,
                                     min_value=1, max_value=100, tag="smoothing_slider", width=250,
                                     format="%d", clamped=True,
                                     callback=lambda s, a: globals().__setitem__('smoothing_factor', a * 10))
                    dpg.bind_item_theme("smoothing_slider", "thin_slider_theme")

                    with dpg.group(horizontal=True):
                        dpg.add_text("Sensitivity", color=(200, 200, 200))
                    dpg.add_slider_float(label="##sensitivity", default_value=sensitivity_multiplier,
                                     min_value=0.1, max_value=2.0, tag="sensitivity_slider", width=250,
                                     format="%.1f", clamped=True,
                                     callback=lambda s, a: globals().__setitem__("sensitivity_multiplier", a))
                    dpg.bind_item_theme("sensitivity_slider", "thin_slider_theme")

                with dpg.group(tag="config_visuals", show=False):
                    dpg.add_separator()
                    dpg.add_spacer(height=5)

                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##box", default_value=hex_to_rgba(esp_box_color),
                                         tag="box_color_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('esp_box_color', a))
                        dpg.add_text("Box", color=(200, 200, 200))

                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##boxoutline", default_value=hex_to_rgba(esp_box_outline_color),
                                         tag="box_outline_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('esp_box_outline_color', a))
                        dpg.add_text("Box Outline", color=(200, 200, 200))

                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##health", default_value=hex_to_rgba(health_bar_outline_color),
                                         tag="health_outline_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('health_bar_outline_color', a))
                        dpg.add_text("Health Bar", color=(200, 200, 200))
                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##chams", default_value=hex_to_rgba(chams_color, chams_transparency),
                                        tag="chams_color_picker", no_inputs=True,
                                        alpha_preview=True, width=10, height=10,
                                        callback=lambda s, a: globals().__setitem__('chams_color', a))
                        dpg.add_text("Chams", color=(200, 200, 200))

                    with dpg.group(horizontal=True):

                        dpg.add_slider_int(label="##chamstrans", default_value=chams_transparency,
                                    min_value=0, max_value=255, tag="chams_trans_slider", width=250,
                                    format="%d", clamped=True,
                                    callback=lambda s, a: globals().__setitem__('chams_transparency', a))
                    dpg.bind_item_theme("chams_trans_slider", "thin_slider_theme")
                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##skeleton", default_value=hex_to_rgba(esp_skeleton_color),
                                         tag="skeleton_color_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('esp_skeleton_color', a))
                        dpg.add_text("Skeleton", color=(200, 200, 200))

                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##skeletonoutline", default_value=hex_to_rgba(esp_skeleton_outline_color),
                                         tag="skeleton_outline_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('esp_skeleton_outline_color', a))
                        dpg.add_text("Skeleton Outline", color=(200, 200, 200))

                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##name", default_value=hex_to_rgba(text_color),
                                         tag="text_color_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('text_color', a))
                        dpg.add_text("Name", color=(200, 200, 200))
                    with dpg.group(horizontal=True):
                     dpg.add_color_edit(label="##tool", default_value=hex_to_rgba(tool_color),
                                      tag="tool_color_picker", no_inputs=True,
                                      no_alpha=True, width=10, height=10,
                                      callback=lambda s, a: globals().__setitem__('tool_color', a))
                     dpg.add_text("Tool", color=(200, 200, 200))

                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##fov", default_value=hex_to_rgba(fov_circle_color),
                                         tag="fov_color_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('fov_circle_color', a))
                        dpg.add_text("FOV Circle", color=(200, 200, 200))

                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##fovoutline", default_value=hex_to_rgba(fov_circle_outline_color),
                                         tag="fov_outline_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('fov_circle_outline_color', a))
                        dpg.add_text("FOV Outline", color=(200, 200, 200))

                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##crosshair", default_value=hex_to_rgba(crosshair_color),
                                         tag="crosshair_color_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('crosshair_color', a))
                        dpg.add_text("Crosshair", color=(200, 200, 200))

                    with dpg.group(horizontal=True):
                        dpg.add_color_edit(label="##crosshairoutline", default_value=hex_to_rgba(crosshair_outline_color),
                                         tag="crosshair_outline_picker", no_inputs=True,
                                         no_alpha=True, width=10, height=10,
                                         callback=lambda s, a: globals().__setitem__('crosshair_outline_color', a))
                        dpg.add_text("Crosshair Outline", color=(200, 200, 200))

                with dpg.group(tag="config_movement", show=False):
                    dpg.add_separator()

                    with dpg.group(horizontal=True):
                        dpg.add_text("Fly Speed", color=(200, 200, 200))
                    dpg.add_slider_int(label="##flyspeed", default_value=fly_speed,
                                     min_value=1, max_value=500, tag="fly_speed_slider", width=250,
                                     format="%d", clamped=True,
                                     callback=lambda s, a: globals().__setitem__('fly_speed', a))
                    dpg.bind_item_theme("fly_speed_slider", "thin_slider_theme")

                    with dpg.group(horizontal=True):
                        dpg.add_text("Orbit Distance", color=(200, 200, 200))
                    dpg.add_slider_float(label="##orbitdistance", default_value=orbit_distance,
                                     min_value=1.0, max_value=100.0, tag="orbit_distance_slider", width=250,
                                     format="%.1f", clamped=True,
                                     callback=lambda s, a: globals().__setitem__("orbit_distance", a))
                    dpg.bind_item_theme("orbit_distance_slider", "thin_slider_theme")

                    with dpg.group(horizontal=True):
                        dpg.add_text("Orbit Speed", color=(200, 200, 200))
                    dpg.add_slider_float(label="##orbitspeed", default_value=orbit_speed,
                                     min_value=1, max_value=100.0, tag="orbit_speed_slider", width=250,
                                     format="%.1f", clamped=True,
                                     callback=lambda s, a: globals().__setitem__("orbit_speed", a))
                    dpg.bind_item_theme("orbit_speed_slider", "thin_slider_theme")

                    
             
                with dpg.group(tag="config_config", show=False):

                    dpg.add_separator()

                    with dpg.group(horizontal=True):

                        dpg.add_button(label=f"{get_key_name(menu_keybind)}",
                                     tag="menu_keybind_button",
                                     callback=lambda: start_keybind_capture("menu"), width=105, height=20)
                        dpg.bind_item_theme("menu_keybind_button", "keybind_button_theme")

   dpg.create_context()

   with dpg.theme() as global_theme:

    with dpg.theme_component(dpg.mvAll):

        dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 255, 255, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, (70, 81, 115, 255))
        dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (20, 22, 26, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (24, 25, 29, 255))
        dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (20, 22, 26, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Border, (40, 43, 49, 255))
        dpg.add_theme_color(dpg.mvThemeCol_BorderShadow, (20, 22, 26, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (28, 32, 39, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (40, 43, 49, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (40, 43, 49, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (12, 14, 18, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (12, 14, 18, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgCollapsed, (20, 22, 26, 255))
        dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, (25, 27, 31, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (12, 14, 18, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (30, 34, 38, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, (40, 43, 49, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive, (30, 34, 38, 255))

        dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (150, 150, 180, 255))
        dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (150, 150, 180, 255))
        dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (150, 150, 180, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ResizeGripHovered, (150, 150, 180, 255))
        dpg.add_theme_color(dpg.mvThemeCol_NavHighlight, (150, 150, 180, 255))
        dpg.add_theme_color(dpg.mvThemeCol_NavWindowingHighlight, (150, 150, 180, 255))

        dpg.add_theme_color(dpg.mvThemeCol_Button, (30, 34, 38, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (46, 48, 50, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (39, 39, 39, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Header, (36, 41, 53, 255))
        dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (27, 27, 27, 255))
        dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (20, 22, 26, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Separator, (33, 38, 49, 255))
        dpg.add_theme_color(dpg.mvThemeCol_SeparatorHovered, (40, 47, 64, 255))
        dpg.add_theme_color(dpg.mvThemeCol_SeparatorActive, (40, 47, 64, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ResizeGrip, (37, 37, 37, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ResizeGripActive, (255, 255, 255, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Tab, (20, 22, 26, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TabHovered, (30, 34, 38, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TabActive, (30, 34, 38, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TabUnfocused, (20, 22, 26, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TabUnfocusedActive, (32, 69, 145, 255))
        dpg.add_theme_color(dpg.mvThemeCol_PlotLines, (133, 153, 179, 255))
        dpg.add_theme_color(dpg.mvThemeCol_PlotLinesHovered, (10, 250, 250, 255))
        dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, (225, 203, 143, 255))
        dpg.add_theme_color(dpg.mvThemeCol_PlotHistogramHovered, (244, 244, 244, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, (12, 14, 18, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong, (12, 14, 18, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, (0, 0, 0, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, (30, 34, 38, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, (25, 27, 31, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TextSelectedBg, (238, 238, 238, 255))
        dpg.add_theme_color(dpg.mvThemeCol_DragDropTarget, (127, 131, 255, 255))
        dpg.add_theme_color(dpg.mvThemeCol_NavWindowingDimBg, (50, 45, 139, 128))
        dpg.add_theme_color(dpg.mvThemeCol_ModalWindowDimBg, (50, 45, 139, 128))

        dpg.add_theme_style(dpg.mvStyleVar_Alpha, 1.0)

        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 12)

        dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0.0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowMinSize, 20, 20)
        dpg.add_theme_style(dpg.mvStyleVar_WindowTitleAlign, 0.5, 0.5)
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0.0)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1.0)
        dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 0.0)
        dpg.add_theme_style(dpg.mvStyleVar_PopupBorderSize, 1.0)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 20, 3.4)

        dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0.0)

        dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 12.1, 9.2)

        dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, 11.6)
        dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, 0.0)
        dpg.add_theme_style(dpg.mvStyleVar_GrabMinSize, 3.7)
        dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 0.0)
        dpg.add_theme_style(dpg.mvStyleVar_TabRounding, 0.0)

        dpg.add_theme_style(dpg.mvStyleVar_ButtonTextAlign, 0.5, 0.5)
        dpg.add_theme_style(dpg.mvStyleVar_SelectableTextAlign, 0.0, 0.0)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 4)

   with dpg.theme(tag="filled_checkbox_theme") as filled_checkbox_theme:
       with dpg.theme_component(dpg.mvCheckbox):
           dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (150, 150, 180, 255))
           dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (28, 32, 39, 255))
           dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (40, 43, 49, 255))
           dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (150, 150, 180, 255))
           dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0.0)

   with dpg.theme(tag="thin_slider_theme") as thin_slider_theme:
       with dpg.theme_component(dpg.mvSliderFloat):
           dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 20, 1)
           dpg.add_theme_style(dpg.mvStyleVar_GrabMinSize, 8)
           dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0.0)
           dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 0.0)
       with dpg.theme_component(dpg.mvSliderInt):
           dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 20, 1)
           dpg.add_theme_style(dpg.mvStyleVar_GrabMinSize, 8)
           dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0.0)
           dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 0.0)

   with dpg.theme(tag="keybind_button_theme") as keybind_button_theme:
       with dpg.theme_component(dpg.mvButton):
           dpg.add_theme_color(dpg.mvThemeCol_Button, (35, 39, 46, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (35, 39, 46, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (35, 39, 46, 255))
           dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0.0)

   with dpg.theme(tag="tab_button_theme") as tab_button_theme:
       with dpg.theme_component(dpg.mvButton):
           dpg.add_theme_color(dpg.mvThemeCol_Button, (20, 22, 26, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (20, 22, 26, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (20, 22, 26, 255))
           dpg.add_theme_color(dpg.mvThemeCol_Text, (150, 150, 180, 255))
           dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0.0)
           dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0.0)

   with dpg.theme(tag="toggle_button_off") as toggle_button_off:
       with dpg.theme_component(dpg.mvButton):
           dpg.add_theme_color(dpg.mvThemeCol_Button, (28, 32, 39, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (28, 32, 39, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (28, 32, 39, 255))
           dpg.add_theme_color(dpg.mvThemeCol_Text, (150, 150, 180, 255))
           dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0.0)
           dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)

   with dpg.theme(tag="toggle_button_on") as toggle_button_on:
       with dpg.theme_component(dpg.mvButton):
           dpg.add_theme_color(dpg.mvThemeCol_Button, (150, 150, 180, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (150, 150, 180, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (150, 150, 180, 255))
           dpg.add_theme_color(dpg.mvThemeCol_Text, (20, 22, 26, 255))
           dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0.0)
           dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)

   with dpg.theme(tag="small_tab_theme") as small_tab_theme:
       with dpg.theme_component(dpg.mvButton):
           dpg.add_theme_color(dpg.mvThemeCol_Button, (20, 22, 26, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (20, 22, 26, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (20, 22, 26, 255))
           dpg.add_theme_color(dpg.mvThemeCol_Text, (130, 130, 160, 255))
           dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0.0)
           dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0.0)

   with dpg.theme(tag="selected_tab_theme") as selected_tab_theme:
       with dpg.theme_component(dpg.mvButton):
           dpg.add_theme_color(dpg.mvThemeCol_Button, (20, 22, 26, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (20, 22, 26, 255))
           dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (20, 22, 26, 255))
           dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 220, 255, 255))
           dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0.0)
           dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0.0)

   with dpg.theme(tag="small_text_theme") as small_text_theme:
       with dpg.theme_component(dpg.mvText):
           dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 5, 0)
   dpg.bind_theme(global_theme)
   dpg.create_viewport(title='PhantomHook', width=300, height=600,decorated=False,resizable=True,  always_on_top=True)
   render_settings_window()

   with dpg.handler_registry():
       dpg.add_mouse_down_handler(callback=mouse_down_callback)
       dpg.add_mouse_drag_handler(callback=mouse_drag_callback)
       dpg.add_mouse_release_handler(callback=mouse_up_callback)

   dpg.setup_dearpygui()
   dpg.set_primary_window("Primary Window", True)
   dpg.show_viewport()
   dpg_hwnd = win32gui.FindWindow(None, "PhantomHook")
def draw_corner_box(x, y, w, h, color, outline_color, corner_length=10):
    corner_length = min(corner_length, w // 3, h // 3)

    pme.draw_line(x - 1, y - 1, x + corner_length + 1, y - 1, outline_color)
    pme.draw_line(x, y, x + corner_length, y, color)
    pme.draw_line(x + 1, y + 1, x + corner_length - 1, y + 1, outline_color)
    pme.draw_line(x - 1, y - 1, x - 1, y + corner_length + 1, outline_color)
    pme.draw_line(x, y, x, y + corner_length, color)
    pme.draw_line(x + 1, y + 1, x + 1, y + corner_length - 1, outline_color)

    pme.draw_line(x + w - corner_length - 1, y - 1, x + w + 1, y - 1, outline_color)
    pme.draw_line(x + w - corner_length, y, x + w, y, color)
    pme.draw_line(x + w - corner_length + 1, y + 1, x + w - 1, y + 1, outline_color)
    pme.draw_line(x + w + 1, y - 1, x + w + 1, y + corner_length + 1, outline_color)
    pme.draw_line(x + w, y, x + w, y + corner_length, color)
    pme.draw_line(x + w - 1, y + 1, x + w - 1, y + corner_length - 1, outline_color)

    pme.draw_line(x - 1, y + h + 1, x + corner_length + 1, y + h + 1, outline_color)
    pme.draw_line(x, y + h, x + corner_length, y + h, color)
    pme.draw_line(x + 1, y + h - 1, x + corner_length - 1, y + h - 1, outline_color)
    pme.draw_line(x - 1, y + h - corner_length - 1, x - 1, y + h + 1, outline_color)
    pme.draw_line(x, y + h - corner_length, x, y + h, color)
    pme.draw_line(x + 1, y + h - corner_length + 1, x + 1, y + h - 1, outline_color)

    pme.draw_line(x + w - corner_length - 1, y + h + 1, x + w + 1, y + h + 1, outline_color)
    pme.draw_line(x + w - corner_length, y + h, x + w, y + h, color)
    pme.draw_line(x + w - corner_length + 1, y + h - 1, x + w - 1, y + h - 1, outline_color)
    pme.draw_line(x + w + 1, y + h - corner_length - 1, x + w + 1, y + h + 1, outline_color)
    pme.draw_line(x + w, y + h - corner_length, x + w, y + h, color)
    pme.draw_line(x + w - 1, y + h - corner_length + 1, x + w - 1, y + h - 1, outline_color)

gid = 0
camCFrameRotAddr = 0
camPosAddr= 0
camAddr = 0
def overlay_thread():
    global fake_ptr,DataModel,Players,Workspace,minimized,Cont,dpg_hwnd,gid,camCFrameRotAddr,camPosAddr,camAddr
    global sticky_target, sticky_target_id, last_aimbot_state
    global aimbot_toggled, _last_aimbot_key_down

    sw, sh = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
    half_w, half_h = sw * 0.5, sh * 0.5
    pme.overlay_init(title="Overlay", fps=10000, exitKey=0)

    if streamproof:
        try:
            time.sleep(0.1)
            overlay_hwnd = win32gui.FindWindow(None, "Overlay")
            if overlay_hwnd:
                WDA_EXCLUDEFROMCAPTURE = 0x00000011
                ctypes.windll.user32.SetWindowDisplayAffinity(overlay_hwnd, WDA_EXCLUDEFROMCAPTURE)

        except Exception as e:
            print(f"Failed to apply StreamProof to overlay: {e}")

    try:
        fake_ptr = pm.read_longlong(baseAddr + int(offsets["FakeDataModelPointer"], 16))
        DataModel = pm.read_longlong(fake_ptr + int(offsets["FakeDataModelToDataModel"], 16))
    except:
        print("[-] Please update roblox! to " + str(offsets["RobloxVersion"]))

        input()

    Workspace = FindFirstChildOfClass(DataModel, "Workspace")
    Cont = pm.read_longlong(Workspace + int(offsets["GravityContainer"],16))
    camAddr = pm.read_longlong(Workspace + int(offsets['Camera'], 16))
    camCFrameRotAddr = camAddr + int(offsets['CameraRotation'], 16)
    camPosAddr = camAddr + int(offsets['CameraPos'], 16)

    memory_aimbot = MemoryAimbot(pm, baseAddr, offsets)

    def find():
        global fake_ptr,DataModel,Players,Workspace,Cont,gid,camCFrameRotAddr,camPosAddr,camAddr
        global fake_ptr,DataModel,Players,Workspace,minimized,Cont,dpg_hwnd,gid,camCFrameRotAddr,camPosAddr,camAddr

        while True:
         try:
            fake_ptre = pm.read_longlong(baseAddr + int(offsets["FakeDataModelPointer"], 16))
            if not is_valid_addr(fake_ptre):
                time.sleep(2)
                continue
            DataModele = pm.read_longlong(fake_ptre + int(offsets["FakeDataModelToDataModel"], 16))
            if not is_valid_addr(DataModele):
                time.sleep(2)
                continue
            namer = GetName(DataModele)

            if namer == "Ugc":

                if DataModele != DataModel:

                    DataModel = DataModele
                    time.sleep(2)
                    Players = FindFirstChildOfClass(DataModel, "Players")
                    Workspace = FindFirstChildOfClass(DataModel, "Workspace")
                    if not is_valid_addr(Workspace):
                        time.sleep(2)
                        continue
                    Cont = pm.read_longlong(Workspace + int(offsets["GravityContainer"],16))
                    visualEngine = pm.read_longlong(baseAddr + int(offsets['VisualEnginePointer'], 16))
                    if is_valid_addr(visualEngine):
                        matrixAddr = visualEngine + int(offsets['viewmatrix'], 16)
                    gid_raw = pm.read_int(DataModel + int(offsets["GameId"],16))
                    gid = gid_raw if isinstance(gid_raw, int) else 0
                    camAddr_new = pm.read_longlong(Workspace + int(offsets['Camera'], 16))
                    if is_valid_addr(camAddr_new):
                        camAddr = camAddr_new
                        camCFrameRotAddr = camAddr + int(offsets['CameraRotation'], 16)
                        camPosAddr = camAddr + int(offsets['CameraPos'], 16)

            time.sleep(2)
         except Exception as e:
             print(f"[find thread] exception: {e}")
             time.sleep(2)

    threading.Thread(target=find,daemon=True).start()
    Players = FindFirstChildOfClass(DataModel, "Players")
    gid = pm.read_int(DataModel + int(offsets["GameId"],16))

    ANGULAR_SPEED = math.radians(90)

    spin_angle = 0.0
    last_time = time.perf_counter()
    hx, hy = win32api.GetCursorPos()

    visualEngine = pm.read_longlong(baseAddr + int(offsets['VisualEnginePointer'], 16))
    matrixAddr = visualEngine + int(offsets['viewmatrix'], 16)

    last_menu_key_state = False
    menu_open= True
    prev_left = False
    aiming = False
    left_down = False
    last_aimbot_state = False

    sticky_target = None
    sticky_target_point = None
    sticky_target_bbox = None

    mw = win32api.GetSystemMetrics(0)
    mh = win32api.GetSystemMetrics(1)

    if streamproof:
        try:
            overlay_hwnd = win32gui.FindWindow(None, "Overlay")
            if overlay_hwnd:
                WDA_EXCLUDEFROMCAPTURE = 0x00000011
                ctypes.windll.user32.SetWindowDisplayAffinity(overlay_hwnd, WDA_EXCLUDEFROMCAPTURE)

        except Exception as e:
            print(f"Failed to apply StreamProof to overlay: {e}")

    while pme.overlay_loop() :
           pme.begin_drawing()
           try:

            if not is_roblox_focused() and not is_menu_focused():
                pme.end_drawing()
                time.sleep(0.01)
                continue

            menu_pressed = (windll.user32.GetAsyncKeyState(menu_keybind) & 0x8000) != 0

            if menu_pressed and not last_menu_key_state:
                menu_open = not menu_open
                if menu_open:
                    win32gui.ShowWindow(dpg_hwnd, win32con.SW_SHOW)
                else:
                    win32gui.ShowWindow(dpg_hwnd, win32con.SW_HIDE)

            last_menu_key_state = menu_pressed

            win_x, win_y, win_width, win_height = get_roblox_window_rect()
            if win_width <= 0 or win_height <= 0:
                pme.end_drawing()
                time.sleep(0.01)
                continue
            half_w = win_width * 0.5
            half_h = win_height * 0.5

            now = time.perf_counter()
            delta_time = now - last_time
            last_time = now
            delta_time = clamp(delta_time, 0.0001, 0.1)
            spin_angle += ANGULAR_SPEED * delta_time
            spin_angle %= math.tau

            cleanup_stale_cache()

            if not is_valid_addr(matrixAddr):
                pme.end_drawing()
                time.sleep(0.01)
                continue
            try:
                raw_vm = bytes(pm.read_bytes(matrixAddr, 64))
                view_matrix = np.frombuffer(raw_vm, dtype=np.float32).reshape(4, 4)
                if not is_valid_view_matrix(view_matrix):
                    pme.end_drawing()
                    time.sleep(0.01)
                    continue
            except Exception:
                pme.end_drawing()
                time.sleep(0.01)
                continue

            local_player = LocalPlayer(Players)
            xx, yy = win32api.GetCursorPos()
            closest_player = None
            closest_distance_sq = float('inf')
            closest_point = None
            closest_3d_point = None
            closest_bbox = None
            LTeam = GetTeamCached(local_player)

            local_hrp_pos = None
            if distance_check_enabled and local_player:
                local_char = GetCharacter(local_player)
                if local_char:
                    local_hrp_pos = get_hrp_position_cached(local_char)

            target_x, target_y = float(xx), float(yy)

            if enable_esp_box or enable_esp_skeleton or enable_health_bar or enable_aimbot or enable_text or enable_chams or enable_tool:
                if not is_valid_addr(Players):
                    pme.end_drawing()
                    time.sleep(0.01)
                    continue
                for player in GetChildren(Players):
                    try:
                        if player == local_player:
                            continue

                        char = GetCharacter(player)

                        if sticky_aim_enabled and sticky_target == player:
                            if not char:
                                sticky_target = None
                                sticky_target_point = None
                                sticky_target_bbox = None
                                continue

                        if not char:
                            continue

                        if team_check:
                            if int(gid) == 1740904786:
                             humd = FindFirstChild(char,"HumanoidRootPart")
                             if humd:
                                 lable = FindFirstChild(humd,"TeammateLabel")
                                 if lable > 0:
                                     continue
                            else:
                             if GetTeamCached(player) == LTeam:
                                continue

                        char_data = GetCharacterData(char)

                        if not char_data:
                            continue

                        humanoid = char_data["humanoid"]
                        if not humanoid:
                            continue

                        health = safe_read_float(lambda: pm.read_float(humanoid + int(offsets["Health"], 16)))
                        max_health = char_data.get("max_health", 100.0)
                        if max_health <= 0 or not math.isfinite(max_health):
                            max_health = 100.0
                        health = clamp(health, 0.0, max_health)

                        if sticky_aim_enabled and sticky_target == player:
                            if health <= 0 or char_data["max_health"] <= 0:
                                sticky_target = None
                                continue

                        if health <= 0 or char_data["max_health"] <= 0:
                            continue

                        display_percent = health / char_data["max_health"]

                        parts_dict = char_data["parts"]

                        if distance_check_enabled and local_hrp_pos is not None:
                            enemy_hrp_pos = get_hrp_position_cached(char)

                            if enemy_hrp_pos is not None:
                                dist_x = enemy_hrp_pos[0] - local_hrp_pos[0]
                                dist_y = enemy_hrp_pos[1] - local_hrp_pos[1]
                                dist_z = enemy_hrp_pos[2] - local_hrp_pos[2]
                                distance = math.sqrt(dist_x*dist_x + dist_y*dist_y + dist_z*dist_z)

                                if distance > max_render_distance:
                                    continue

                        positions_np = np.array([Position(p) for p in parts_dict.values()], dtype=np.float32)
                        name_to_idx = {name: i for i, name in enumerate(parts_dict.keys())}
                        bones = R15_BONES if char_data["is_r15"] else R6_BONES

                        screen_points = batch_world_to_screen(positions_np, view_matrix, half_w, half_h)
                        screens = {
                            name: screen_points[idx]
                            for name, idx in name_to_idx.items()
                            if screen_points[idx] is not None
                        }

                        if not screens:
                            continue

                        bbox = calculate_perfect_bounding_box(
                            parts_dict,
                            positions_np,
                            name_to_idx,
                            view_matrix,
                            half_w,
                            half_h,
                            char_data["is_r15"]
                        )

                        if not bbox:
                            continue

                        box_x = bbox['x']
                        box_y = bbox['y']
                        box_w = bbox['w']
                        box_h = bbox['h']
                        if enable_chams:

                            all_screen_faces = []
                            all_screen_edges = []
                            all_world_corners = {}
                            GetPrim = GetPrimitive

                            all_parts_data = []
                            all_corners_flat = []
                            corner_indices = {}
                            current_idx = 0

                            for part_name, part_addr in parts_dict.items():

                                prim = GetPrim(part_addr)
                                if prim:

                                    geometry = get_chams_geometry(prim)

                                    transform = get_chams_transform(prim)

                                    if geometry is not None and transform is not None:

                                        local_corners = geometry['local_corners']
                                        position = transform['position']
                                        rotation = transform['rotation']

                                        world_corners = local_corners @ rotation.T
                                        world_corners += position

                                        all_world_corners[part_name] = world_corners
                                        all_parts_data.append(part_name)
                                        all_corners_flat.extend(world_corners)
                                        corner_indices[part_name] = (current_idx, current_idx + 8)
                                        current_idx += 8

                            if len(all_corners_flat) > 0:

                                all_corners_array = np.array(all_corners_flat, dtype=np.float32)
                                all_screen_corners = batch_world_to_screen(
                                    all_corners_array, view_matrix, half_w, half_h
                                )

                                for part_name in all_parts_data:
                                    start_idx, end_idx = corner_indices[part_name]
                                    screen_corners = all_screen_corners[start_idx:end_idx]

                                    visible = [pt is not None for pt in screen_corners]

                                    if chams_mode == "Filled":
                                        for a, b, c, d in CHAMS_FACES:
                                            if visible[a] and visible[b] and visible[c] and visible[d]:
                                                all_screen_faces.append([
                                                    screen_corners[a],
                                                    screen_corners[b],
                                                    screen_corners[c],
                                                    screen_corners[d]
                                                ])

                                    elif chams_mode == "Outline":
                                        for a, b in CHAMS_EDGES:
                                            if visible[a] and visible[b]:
                                                all_screen_edges.append([
                                                    screen_corners[a],
                                                    screen_corners[b]
                                                ])

                            if chams_mode == "Filled" and len(all_screen_faces) > 0:
                                alpha_value = chams_transparency
                                color = pme.new_color(*hex_to_rgba(chams_color, alpha_value))
                                outline_color = pme.new_color(0, 0, 0, 255)

                                draw_tri = pme.draw_triangle
                                draw_line = pme.draw_line

                                for face in all_screen_faces:
                                    x0, y0 = face[0]
                                    x1, y1 = face[1]
                                    x2, y2 = face[2]
                                    x3, y3 = face[3]

                                    draw_line(x0, y0, x1, y1, outline_color)
                                    draw_line(x1, y1, x2, y2, outline_color)
                                    draw_line(x2, y2, x3, y3, outline_color)
                                    draw_line(x3, y3, x0, y0, outline_color)

                                for face in all_screen_faces:
                                    x0, y0 = face[0]
                                    x1, y1 = face[1]
                                    x2, y2 = face[2]
                                    x3, y3 = face[3]

                                    draw_tri(x0, y0, x1, y1, x2, y2, color)
                                    draw_tri(x0, y0, x2, y2, x3, y3, color)

                            elif chams_mode == "Outline" and len(all_screen_edges) > 0:
                                alpha_value = chams_transparency
                                main_color = pme.new_color(*hex_to_rgba(chams_color, alpha_value))
                                outline_color = pme.new_color(0, 0, 0, 255)

                                draw_line = pme.draw_line

                                for edge in all_screen_edges:
                                    x0, y0 = edge[0]
                                    x1, y1 = edge[1]
                                    draw_line(x0, y0, x1, y1, main_color)

                                outline_offsets = [(1, 0), (0, 1)]
                                for edge in all_screen_edges:
                                    x0, y0 = edge[0]
                                    x1, y1 = edge[1]
                                    for ox, oy in outline_offsets:
                                        draw_line(x0 + ox, y0 + oy, x1 + ox, y1 + oy, outline_color)

                        if enable_esp_skeleton:
                            valid_bone_lines = []
                            for a, b in bones:
                                if a in screens and b in screens:
                                    valid_bone_lines.append((screens[a], screens[b]))

                            if valid_bone_lines:
                                skel_color = pme.new_color(*hex_to_rgba(esp_skeleton_color))
                                skel_outline = pme.new_color(*hex_to_rgba(esp_skeleton_outline_color))
                                outline_offsets = [
                                    ( 1,  0), (-1,  0), ( 0,  1), ( 0, -1),
                                    ( 1,  1), ( 1, -1), (-1,  1), (-1, -1)
                                ]

                                for p1, p2 in valid_bone_lines:
                                    for ox, oy in outline_offsets:
                                        pme.draw_line(
                                            p1[0] + ox, p1[1] + oy,
                                            p2[0] + ox, p2[1] + oy,
                                            skel_outline
                                        )
                                for p1, p2 in valid_bone_lines:
                                    pme.draw_line(
                                        p1[0], p1[1],
                                        p2[0], p2[1],
                                        skel_color
                                    )

                        if enable_tool:
                         tool = FindFirstChildOfClass(char,"Tool")
                         if tool:
                             text = GetName(tool)

                             text_width = pme.measure_text(text, 2)
                             text_x = box_x + (box_w // 2) - (text_width // 2)
                             text_y = box_y + box_h + 1

                             pme.draw_text(
                                 text,
                                 text_x + 1,
                                 text_y + 1,
                                 2,
                                 pme.new_color(0, 0, 0, 255)
                             )

                             pme.draw_text(
                                 text,
                                 text_x,
                                 text_y,
                                 2,
                                 pme.new_color(*hex_to_rgba(tool_color))
                             )
                         else:
                             text = "None"

                             text_width = pme.measure_text(text, 2)
                             text_x = box_x + (box_w // 2) - (text_width // 2)
                             text_y = box_y + box_h + 1

                             pme.draw_text(
                                 text,
                                 text_x + 1,
                                 text_y + 1,
                                 2,
                                 pme.new_color(0, 0, 0, 255)
                             )

                             pme.draw_text(
                                 text,
                                 text_x,
                                 text_y,
                                 2,
                                 pme.new_color(*hex_to_rgba(tool_color))
                             )
                        if enable_text:
                            text = GetName(player)

                            text_width = pme.measure_text(text, 2)
                            text_x = box_x + (box_w // 2) - (text_width // 2)
                            text_y = box_y - 11

                            pme.draw_text(
                                text,
                                text_x + 1,
                                text_y + 1,
                                2,
                                pme.new_color(0, 0, 0, 255)
                            )

                            pme.draw_text(
                                text,
                                text_x,
                                text_y,
                                2,
                                pme.new_color(*hex_to_rgba(text_color))
                            )
                        if enable_esp_box:
                                            box_rgba = hex_to_rgba(esp_box_color)
                                            outline_rgba = hex_to_rgba(esp_box_outline_color)

                                            box_color = pme.new_color(*box_rgba)
                                            outline_color = pme.new_color(*outline_rgba)

                                            if box_mode == "Full":
                                                pme.draw_rectangle_lines(box_x - 1, box_y - 1, box_w + 2, box_h + 2, outline_color, 1.0)

                                                pme.draw_rectangle_lines(box_x, box_y, box_w, box_h, box_color, 1.0)

                                                pme.draw_rectangle_lines(box_x + 1, box_y + 1, box_w - 2, box_h - 2, outline_color, 1.0)
                                            elif box_mode == "Corners":
                                                corner_length = min(max(box_w, box_h) // 4, 15)
                                                draw_corner_box(box_x, box_y, box_w, box_h, box_color, outline_color, corner_length)

                        if enable_health_bar:
                              bar_width = 1
                              bar_x = box_x - 4
                              bar_y = box_y
                              bar_h = box_h

                              pme.draw_rectangle_lines(
                                  bar_x - 1, bar_y - 1,
                                  bar_width + 2, bar_h + 2,
                                  pme.new_color(*hex_to_rgba(health_bar_outline_color)),
                                  1.0
                              )

                              fill_h = int(bar_h * display_percent)
                              if display_percent >= 0.5:
                                  r = int(255 * (1 - display_percent) * 2)
                                  g = 255
                                  b = 0
                              else:
                                  r = 255
                                  g = int(255 * display_percent * 2)
                                  b = 0

                              pme.draw_rectangle(
                                  bar_x,
                                  bar_y + bar_h - fill_h,
                                  bar_width,
                                  fill_h,
                                  pme.new_color(r, g, b, 255)
                              )
                        if enable_aimbot and hit_parts:
                            if sticky_aim_enabled and sticky_target is not None:
                             if player != sticky_target:
                                continue
                            target_parts = []
                            for category in hit_parts:
                                if category == "Head":
                                    target_parts.append("Head")
                                elif category == "Arms":
                                    target_parts.extend(["LeftUpperArm", "LeftLowerArm", "LeftHand",
                                                        "RightUpperArm", "RightLowerArm", "RightHand"])
                                    target_parts.extend(["Left Arm", "Right Arm"])
                                elif category == "Torso":
                                    target_parts.extend(["UpperTorso", "LowerTorso"])
                                    target_parts.append("Torso")
                                elif category == "Legs":
                                    target_parts.extend(["LeftUpperLeg", "LeftLowerLeg", "LeftFoot",
                                                        "RightUpperLeg", "RightLowerLeg", "RightFoot"])
                                    target_parts.extend(["Left Leg", "Right Leg"])

                            for part_name in target_parts:
                                    if part_name in screens:
                                        sx, sy = screens[part_name]
                                        dist_sq = (sx - xx) ** 2 + (sy - yy) ** 2

                                        if use_fov:
                                            if dist_sq < closest_distance_sq and math.sqrt(dist_sq) < fov_radius:
                                                closest_distance_sq = dist_sq
                                                closest_point = (sx, sy)
                                                closest_3d_point = positions_np[name_to_idx[part_name]]
                                                closest_bbox = bbox
                                                closest_player = player
                                        else:
                                            if dist_sq < closest_distance_sq:
                                                closest_distance_sq = dist_sq
                                                closest_point = (sx, sy)
                                                closest_3d_point = positions_np[name_to_idx[part_name]]
                                                closest_bbox = bbox
                                                closest_player = player

                    except Exception as _player_err:
                        pass
            crosshair_x = float(xx)
            crosshair_y = float(yy)
            _raw_key_down = win32api.GetKeyState(aimbot_keybind) < 0 if aimbot_keybind is not None else False

            if aimbot_trigger_mode == "Toggle":
                if _raw_key_down and not _last_aimbot_key_down:
                    aimbot_toggled = not aimbot_toggled
                left_down = aimbot_toggled
            else:
                left_down = _raw_key_down
                if not left_down:
                    aimbot_toggled = False
            _last_aimbot_key_down = _raw_key_down

            if not left_down and sticky_target is not None:
                sticky_target = None
            elif left_down and not last_aimbot_state and sticky_target is not None:
                sticky_target = None

            if sticky_aim_enabled and left_down and sticky_target is None and closest_player is not None:
                sticky_target = closest_player

            last_aimbot_state = left_down

            if enable_aimbot:
                target_screen = None
                if closest_point:
                    target_screen = project_world_to_screen(closest_3d_point, view_matrix, half_w, half_h)
                    if target_screen:
                        target_x, target_y = target_screen
                    else:
                        target_x, target_y = float(closest_point[0]), float(closest_point[1])
                hx = smooth_lerp_dt(hx, target_x, delta_time, speed=60.0)
                hy = smooth_lerp_dt(hy, target_y, delta_time, speed=60.0)
                if enable_crosshair:
                    crosshair_x, crosshair_y = hx, hy
            elif enable_crosshair:
                crosshair_x, crosshair_y = float(xx), float(yy)

            if enable_fov_circle:
                pme.draw_circle_lines(
                    xx, yy, fov_radius + 1,
                    pme.new_color(*hex_to_rgba(fov_circle_outline_color))
                )

                pme.draw_circle_lines(
                    xx, yy, fov_radius,
                    pme.new_color(*hex_to_rgba(fov_circle_color))
                )

                pme.draw_circle_lines(
                    xx, yy, fov_radius - 1,
                    pme.new_color(*hex_to_rgba(fov_circle_outline_color))
                )

            if enable_crosshair:
                draw_spinning_crosshair(
                    crosshair_x,
                    crosshair_y,
                    size=14,
                    gap=6,
                    spin_angle=spin_angle,
                    color=pme.new_color(*hex_to_rgba(crosshair_color)),
                    outline=pme.new_color(*hex_to_rgba(crosshair_outline_color))
                )

            if left_down and enable_aimbot and closest_point is not None and aim_mode == "Silent":
                    if is_valid_addr(camAddr):
                        monitor_width = mw
                        monitor_height = mh

                        target_screen_x = closest_point[0]
                        target_screen_y = closest_point[1]

                        if (0 <= target_screen_x <= monitor_width and
                                0 <= target_screen_y <= monitor_height):

                            current_hitchance = calculate_hitchance(
                                xx, yy,
                                target_screen_x, target_screen_y,
                                fov_radius
                            )

                            if silent_hitchance >= current_hitchance:
                                final_target_x = target_screen_x
                                final_target_y = target_screen_y

                                target_vp_x = 2 * (monitor_width - final_target_x)
                                target_vp_y = 2 * (monitor_height - final_target_y)

                                x_value = clamp(int(target_vp_x), -32768, 32767)
                                y_value = clamp(int(target_vp_y), -32768, 32767)

                                try:
                                    pm.write_short(camAddr + 0x2ac, x_value)
                                    pm.write_short(camAddr + 0x2ac + 0x2, y_value)
                                except Exception as _e:
                                    print(f"[silent aim] write failed: {_e}")

            if enable_aimbot and left_down and closest_point and closest_3d_point is not None and is_valid_position(closest_3d_point):
             if aim_mode == "Mouse":

                 target_screen = project_world_to_screen(closest_3d_point, view_matrix, half_w, half_h)
                 aim_target_x, aim_target_y = target_screen if target_screen else closest_point

                 cur_x, cur_y = win32api.GetCursorPos()

                 should_move_mouse = True
                 if legit_mode and closest_bbox is not None:
                     if is_point_inside_bbox(cur_x, cur_y, closest_bbox):
                         should_move_mouse = False

                 if should_move_mouse:
                     dx = aim_target_x - cur_x
                     dy = aim_target_y - cur_y

                     if use_smooth:
                        lerp_factor = 1.0 - math.exp(-smoothing_factor * delta_time)
                        smooth_dx = dx * lerp_factor
                        smooth_dy = dy * lerp_factor
                     else:
                        smooth_dx = dx
                        smooth_dy = dy

                     smooth_dx *= sensitivity_multiplier
                     smooth_dy *= sensitivity_multiplier
                     if abs(smooth_dx) > 0.05 or abs(smooth_dy) > 0.05:
                         send_input_move(smooth_dx, smooth_dy)

             elif aim_mode == "Memory":
              if use_smooth:
                  memory_aimbot.aim_at_target(
                      closest_3d_point,
                      DataModel,
                      delta_time,
                      smoothing_factor
                  )
              else:
                      if not memory_aimbot.update_addresses(DataModel):
                          pass
                      else:
                          cam_pos_bytes = pm.read_bytes(memory_aimbot.cam_pos_addr, 12)
                          cam_pos = np.frombuffer(cam_pos_bytes, dtype=np.float32)

                          dx = closest_3d_point[0] - cam_pos[0]
                          dy = closest_3d_point[1] - cam_pos[1]
                          dz = closest_3d_point[2] - cam_pos[2]

                          mag = (dx*dx + dy*dy + dz*dz) ** 0.5
                          if mag >= 1e-6:
                              inv_mag = 1.0 / mag
                              lx, ly, lz = dx * inv_mag, dy * inv_mag, dz * inv_mag

                              if abs(ly) > 0.999:
                                  ux, uy, uz = 0.0, 0.0, -1.0
                              else:
                                  ux, uy, uz = 0.0, 1.0, 0.0

                              rx = uy * lz - uz * ly
                              ry = uz * lx - ux * lz
                              rz = ux * ly - uy * lx

                              mag_r = (rx*rx + ry*ry + rz*rz) ** 0.5
                              inv_mag_r = 1.0 / mag_r
                              rx, ry, rz = rx * inv_mag_r, ry * inv_mag_r, rz * inv_mag_r

                              ux = ly * rz - lz * ry
                              uy = lz * rx - lx * rz
                              uz = lx * ry - ly * rx

                              rotation_data = np.array([
                                  -rx, ux, -lx,
                                  -ry, uy, -ly,
                                  -rz, uz, -lz
                              ], dtype=np.float32)

                              if (np.all(np.isfinite(rotation_data)) and
                                      is_valid_addr(memory_aimbot.cam_rot_addr)):
                                  try:
                                      pm.write_bytes(memory_aimbot.cam_rot_addr, rotation_data.tobytes(), 36)
                                  except Exception as _e:
                                      print(f"[mem aim] write failed: {_e}")

            pme.end_drawing()

           except Exception as e:
             print(f"[overlay loop] exception: {e}")
             pme.end_drawing()
             continue

           if dpg.is_dearpygui_running():
               dpg.render_dearpygui_frame()

    dpg.destroy_context()

if __name__ == '__main__':
    Render()
    overlay_thread()
