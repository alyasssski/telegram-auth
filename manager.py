#!/usr/bin/env python3

import os
import time
import json
import struct
import sqlite3
import subprocess
import select
import asyncio
import math
import socket
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

from flask import Flask, request
from flask_restx import Api, Resource, fields
from telethon import TelegramClient
from telethon.sessions import MemorySession
from telethon.errors import SessionPasswordNeededError
from telethon.crypto import AuthKey

# КОНФИГУРАЦИЯ 
SESSIONS_DIR = Path("./sessions")
SESSIONS_DIR.mkdir(exist_ok=True)
ADB_DEVICE = "localhost:5555"

# TELEGRAM API КОНФИГУРАЦИЯ 
TELEGRAM_API_ID = ...
TELEGRAM_API_HASH = "..."

# Flask и Swagger 
app = Flask(__name__)
api = Api(
    app,
    version='1.0',
    title='Telegram Auth Extractor API',
    description='Ручной режим - ввод данных через scrcpy',
    doc='/swagger/',
    prefix='/api'
)

# МОДЕЛИ ДЛЯ SWAGGER 
auth_start_model = api.model('AuthStart', {
    'phone': fields.String(required=True, description='Номер телефона в формате +7')
})

auth_verify_model = api.model('AuthVerify', {
    'phone': fields.String(required=True),
    'code': fields.String(required=False, description='Код подтверждения'),
    'password': fields.String(required=False, description='Пароль')
})

extract_model = api.model('Extract', {
    'phone': fields.String(required=True),
    'username': fields.String(required=False, description='Имя пользователя')
})

test_session_model = api.model('TestSession', {
    'auth_key': fields.String(required=True, description='Auth key в hex формате'),
    'dc_id': fields.Integer(required=True, description='ID дата-центра'),
    'user_id': fields.Integer(required=True, description='ID пользователя'),
    'phone': fields.String(required=False, description='Номер телефона')
})

# КЛАСС ПРОВЕРКИ ИНФРАСТРУКТУРЫ 

class InfrastructureManager:
    """Класс для проверки и настройки всей инфраструктуры"""
    
    def __init__(self):
        self.docker_available = self._check_docker()
        
    def _check_docker(self) -> bool:
        """Проверка доступности Docker"""
        try:
            result = subprocess.run(["docker", "--version"], capture_output=True, text=True)
            return result.returncode == 0
        except:
            return False
    
    def check_android_container(self) -> Dict[str, Any]:
        """Проверка наличия запущенного Android контейнера"""
        result = {
            'running': False,
            'container_name': None,
            'container_id': None,
            'action_taken': None
        }
        
        print("\nПРОВЕРКА ANDROID КОНТЕЙНЕРА:")
        
        if not self.docker_available:
            print("Docker не доступен, Android запущен вручную")
            result['running'] = True
            result['container_name'] = 'android'
            return result
        
        try:
            # Ищем redroid контейнеры
            ps_result = subprocess.run(
                "docker ps --format '{{.Names}}' | grep -E 'redroid|android'",
                shell=True, capture_output=True, text=True
            )
            
            containers = ps_result.stdout.strip().split('\n')
            for container in containers:
                if container and ('redroid' in container or 'android' in container):
                    result['running'] = True
                    result['container_name'] = container
                    print(f"Найден запущенный контейнер: {container}")
                    
                    # Получаем ID
                    id_result = subprocess.run(
                        f"docker ps --filter name={container} --format '{{{{.ID}}}}'",
                        shell=True, capture_output=True, text=True
                    )
                    result['container_id'] = id_result.stdout.strip()
                    break
            
            if not result['running']:
                print("Запущенный Android контейнер не найден")
                print("Запуск нового контейнера...")
                
                # Запускаем контейнер
                self._start_android_container()
                result['running'] = True
                result['container_name'] = 'redroid12'
                result['action_taken'] = 'started_new_container'
                
        except Exception as e:
            print(f"Ошибка при проверке: {e}")
            result['error'] = str(e)
        
        return result
    
    def _start_android_container(self):
        """Запуск Android контейнера"""
        print("\nЗапуск Android контейнера:")
        
        # Создаем директорию для данных если нет
        home = os.path.expanduser("~")
        data_dir = os.path.join(home, "data")
        os.makedirs(data_dir, exist_ok=True)
        
        # Запускаем контейнер
        cmd = (
            "docker run -itd --rm --privileged "
            "--pull always "
            "--name redroid12 "
            f"-v {data_dir}:/data "
            "-p 5555:5555 "
            "redroid/redroid:12.0.0_64only-latest"
        )
        
        print(f"  Выполняю: {cmd}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode == 0:
            print("Контейнер запущен")
            print("Ожидание инициализации Android...")
            time.sleep(30)
        else:
            print(f"Ошибка: {result.stderr}")
            raise Exception("Не удалось запустить Android контейнер")
    
    def check_adb_connection(self) -> bool:
        """Проверка и настройка ADB подключения"""
        print("\nПРОВЕРКА ADB ПОДКЛЮЧЕНИЯ:")
        
        # Получаем текущее значение глобальной переменной
        import sys
        current_device = sys.modules['__main__'].ADB_DEVICE
        
        # Пробуем подключиться
        connect_result = subprocess.run(
            f"adb connect {current_device}",
            shell=True, capture_output=True, text=True
        )
        
        if "connected" in connect_result.stdout:
            print(f"ADB подключен к {current_device}")
            return True
        
        print(f"Не удалось подключиться к {current_device}")
        print("Пробую альтернативные методы...")
        
        # Пробуем localhost:5555
        alt_result = subprocess.run(
            "adb connect localhost:5555",
            shell=True, capture_output=True, text=True
        )
        
        if "connected" in alt_result.stdout:
            print("ADB подключен к localhost:5555")
            # Обновляем глобальную переменную
            sys.modules['__main__'].ADB_DEVICE = "localhost:5555"
            # Также обновляем в текущем модуле
            globals()['ADB_DEVICE'] = "localhost:5555"
            return True
        
        print("Не удалось подключиться к ADB")
        return False
    
    def check_telegram_installed(self) -> bool:
        """Проверка и установка Telegram"""
        print("\nПРОВЕРКА TELEGRAM:")
        
        # Получаем текущее устройство
        import sys
        current_device = sys.modules['__main__'].ADB_DEVICE
        
        # Проверяем установлен ли Telegram
        result = subprocess.run(
            f"adb -s {current_device} shell pm list packages | grep org.telegram.messenger",
            shell=True, capture_output=True, text=True
        )
        
        if result.stdout.strip():
            print("Telegram уже установлен")
            return True
        
        print("Telegram не найден")
        print("Установка Telegram...")
        
        # Скачиваем APK если нет
        apk_path = "/tmp/telegram.apk"
        if not Path(apk_path).exists():
            print("Установка Telegram APK...")
            subprocess.run(
                "wget https://telegram.org/dl/android/apk -O /tmp/telegram.apk",
                shell=True, check=True
            )
        
        # Устанавливаем
        install_result = subprocess.run(
            f"adb -s {current_device} install {apk_path}",
            shell=True, capture_output=True, text=True
        )
        
        if install_result.returncode == 0:
            print("Telegram установлен")
            return True
        else:
            print(f"Ошибка установки: {install_result.stderr}")
            # Пробуем переустановить
            reinstall_result = subprocess.run(
                f"adb -s {current_device} install -r {apk_path}",
                shell=True, capture_output=True, text=True
            )
            if reinstall_result.returncode == 0:
                print("Telegram переустановлен")
                return True
            return False
    
    def setup_all(self) -> bool:
        """Полная настройка всей инфраструктуры"""
        print("\n")
        print("ПРОВЕРКА И НАСТРОЙКА ИНФРАСТРУКТУРЫ")
       
        
        # 1. Проверяем Android контейнер
        container_info = self.check_android_container()
        if not container_info['running']:
            print("Не удалось запустить Android контейнер")
            return False
        
        # 2. Проверяем ADB подключение
        if not self.check_adb_connection():
            print("Не удалось подключиться по ADB")
            return False
        
        # Ждем полной загрузки Android
        print("\nОжидание полной загрузки Android...")
        time.sleep(5)
        
        # 3. Проверяем Telegram
        if not self.check_telegram_installed():
            print("Не удалось установить Telegram")
            return False
        
        print("\n")
        print(f"Android: {container_info['container_name']}")
        print(f"ADB: {ADB_DEVICE}")
        print(f"Telegram: установлен")
        
        return True

# ADB ФУНКЦИИ 

def adb(command: str) -> tuple[bool, str]:
    """Выполнить обычную ADB команду (без root)"""
    full_cmd = f"adb -s {ADB_DEVICE} {command}"
    try:
        result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout.strip()
    except Exception as e:
        return False, str(e)

def adb_root_command(commands: List[str], timeout: int = 30) -> tuple[bool, str]:
    """
    Выполнить последовательность команд с root доступом
    """
    try:
        process = subprocess.Popen(
            ["adb", "-s", ADB_DEVICE, "shell"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        full_output = ""
        
        process.stdin.write("su\n")
        process.stdin.flush()
        time.sleep(1)
        
        while True:
            ready, _, _ = select.select([process.stdout], [], [], 1)
            if ready:
                line = process.stdout.readline()
                full_output += line
                if "#" in line:
                    break
            else:
                break
        
        for cmd in commands:
            process.stdin.write(f"{cmd}\n")
            process.stdin.flush()
            time.sleep(2)
            
            cmd_output = ""
            while True:
                ready, _, _ = select.select([process.stdout], [], [], 2)
                if ready:
                    line = process.stdout.readline()
                    if not line or line.strip() == "":
                        break
                    cmd_output += line
                else:
                    break
            full_output += cmd_output
        
        process.stdin.write("exit\nexit\n")
        process.stdin.flush()
        process.stdin.close()
        process.wait(timeout=5)
        
        return True, full_output.strip()
        
    except Exception as e:
        print(f"Ошибка в adb_root_command: {e}")
        return False, ""
    finally:
        try:
            process.terminate()
        except:
            pass

def check_adb() -> bool:
    """Проверить подключение к Android"""
    success, output = adb("get-state")
    return success and "device" in output

def telegram_installed() -> bool:
    """Проверить, установлен ли Telegram"""
    success, output = adb("shell pm list packages | grep org.telegram.messenger")
    return bool(output.strip())

def clear_telegram() -> None:
    """Очистить данные Telegram"""
    print("Очистка данных Telegram...")
    adb("shell pm clear org.telegram.messenger.web")
    time.sleep(2)

def launch_telegram() -> None:
    """Только запуск, без автоматических нажатий"""
    print("Запуск Telegram...")
    adb("shell am start -n org.telegram.messenger.web/org.telegram.ui.LaunchActivity")
    print("\n")
    print("Инструкция:")
    print("1. Откройте scrcpy в другом окне:")
    print("   adb connect localhost:5555")
    print("   scrcpy -s localhost:5555")
    print("2. Введите номер телефона")
    print("3. Введите код")
    print("4. После авторизации используйте /api/auth/extract")
    print("\n")

def is_authorized() -> bool:
    """Проверить наличие tgnet.dat"""
    try:
        print("Проверка авторизации...")
        commands = ["ls -la /data/data/org.telegram.messenger.web/files/tgnet.dat"]
        success, output = adb_root_command(commands, timeout=10)
        
        if success and "tgnet.dat" in output and "No such file" not in output:
            import re
            size_match = re.search(r'\s+(\d+)\s+', output)
            if size_match:
                file_size = size_match.group(1)
                print(f"tgnet.dat найден, размер: {file_size} байт")
            else:
                print(f"tgnet.dat найден")
            return True
        else:
            print(f"tgnet.dat не найден")
            return False
    except Exception as e:
        print(f"Ошибка при проверке: {e}")
        return False

def pull_file(remote: str, local: str) -> bool:
    """Скопировать файл с Android"""
    try:
        print(f"Копирование {remote}...")
        
        commands = [
            f"cp {remote} /data/local/tmp/temp_file.dat",
            "chmod 644 /data/local/tmp/temp_file.dat",
            f"ls -la /data/local/tmp/temp_file.dat"
        ]
        
        success, output = adb_root_command(commands, timeout=15)
        
        if not success or "temp_file.dat" not in output:
            print("Не удалось скопировать файл во временную директорию")
            return False
        
        time.sleep(2)
        
        pull_cmd = f"adb -s {ADB_DEVICE} pull /data/local/tmp/temp_file.dat {local}"
        print(f"Выполняем: {pull_cmd}")
        result = subprocess.run(pull_cmd, shell=True, capture_output=True, text=True, timeout=30)
        
        cleanup_commands = ["rm /data/local/tmp/temp_file.dat"]
        adb_root_command(cleanup_commands, timeout=5)
        
        if result.returncode == 0 and Path(local).exists():
            local_size = Path(local).stat().st_size
            print(f"Файл скопирован: {local} ({local_size} байт)")
            return True
        else:
            print(f"Не удалось скопировать файл: {result.stderr}")
            return False
    except Exception as e:
        print(f"Ошибка при копировании: {e}")
        return False

def wait_for_authorization(timeout_seconds: int = 60) -> bool:
    """Ожидание авторизации"""
    print(f"Ожидание авторизации (макс. {timeout_seconds} сек)...")
    for i in range(timeout_seconds):
        if is_authorized():
            print(f"Авторизация подтверждена через {i+1} сек")
            return True
        if i % 10 == 0:
            print(f"   ... прошло {i} сек")
        time.sleep(1)
    print(f"Таймаут ожидания авторизации")
    return False

# ФУНКЦИИ АНАЛИЗА 

def calculate_entropy(data: bytes) -> float:
    """Рассчитать энтропию Шеннона для байтов"""
    if not data:
        return 0.0
    entropy = 0
    for x in range(256):
        p_x = data.count(x) / len(data)
        if p_x > 0:
            entropy += - p_x * math.log2(p_x)
    return entropy

# ИЗВЛЕЧЕНИЕ ДАННЫХ 

def extract_session(phone: str, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Извлечение данных сессии - исправленная версия с правильным dc_id"""
    print(f"\nИзвлечение данных для {phone}...")
    
    if not is_authorized():
        print("Telegram не авторизован")
        return None
    
    # Временные файлы
    tgnet_local = SESSIONS_DIR / f"tgnet_{phone}.dat"
    cache_local = SESSIONS_DIR / f"cache_{phone}.db"
    
    # Копируем tgnet.dat
    if not pull_file(
        "/data/data/org.telegram.messenger.web/files/tgnet.dat",
        str(tgnet_local)
    ):
        print("Не удалось скопировать tgnet.dat")
        return None
    
    # Проверяем, что файл скопировался и имеет достаточный размер
    if not tgnet_local.exists() or tgnet_local.stat().st_size < 1024:
        print(f"tgnet.dat слишком маленький: {tgnet_local.stat().st_size} байт")
        tgnet_local.unlink(missing_ok=True)
        return None
    
    # Копируем cache4.db 
    print("Копирование cache4.db...")
    has_cache = pull_file(
        "/data/data/org.telegram.messenger.web/files/cache4.db",
        str(cache_local)
    )
    
    # Парсим tgnet.dat
    with open(tgnet_local, 'rb') as f:
        data = f.read()
    
    print(f"Размер tgnet.dat: {len(data)} байт")
    
    # Ищем auth_key по сигнатуре 
    auth_key_sig = b'\x4f\x0a\x4b\x83'
    auth_key_start = data.find(auth_key_sig)
    
    if auth_key_start != -1 and len(data) > auth_key_start + 256:
        auth_key = data[auth_key_start:auth_key_start+256]
        print(f"Найден auth_key на смещении 0x{auth_key_start:x}")
    else:
        # Пробуем стандартное смещение 256
        if len(data) > 512:
            auth_key = data[256:512]
            auth_key_start = 256
            print("Сигнатура не найдена, используем смещение 256")
        else:
            print("Не удалось найти auth_key")
            tgnet_local.unlink(missing_ok=True)
            return None
    
    # Сохраняем ключ в отдельный файл
    key_file = SESSIONS_DIR / f"key_{phone}_{auth_key_start:04x}.key"
    with open(key_file, 'w') as f:
        f.write(auth_key.hex())
    print(f"Ключ сохранен в {key_file}")
    
    
    dc_id = None
    try:
        # Пробуем несколько возможных смещений где может быть dc_id
        possible_offsets = [0x66, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24]
        
        for offset in possible_offsets:
            if len(data) > offset + 4:
                candidate = struct.unpack('<I', data[offset:offset+4])[0]
                # Проверяем что это валидный dc_id (1-5)
                if 1 <= candidate <= 5:
                    dc_id = candidate
                    print(f"Найден валидный dc_id={dc_id} на смещении 0x{offset:x}")
                    break
        
        # Если не нашли, пробуем найти рядом с ключом
        if dc_id is None:
            for i in range(max(0, auth_key_start - 32), min(len(data) - 4, auth_key_start + 256)):
                try:
                    candidate = struct.unpack('<I', data[i:i+4])[0]
                    if 1 <= candidate <= 5:
                        dc_id = candidate
                        print(f"Найден dc_id={dc_id} рядом с ключом на смещении 0x{i:x}")
                        break
                except:
                    continue
        
        # Если все еще не нашли, используем DC по умолчанию
        if dc_id is None:
            dc_id = 2
            print(f"dc_id не найден, используем по умолчанию: {dc_id}")
            
    except Exception as e:
        print(f"Ошибка при поиске dc_id: {e}")
        dc_id = 2
        print(f"Использую dc_id по умолчанию: {dc_id}")
    
    # Парсим cache4.db для получения user_id
    user_id = None
    extracted_username = None
    
    if has_cache and cache_local.exists() and cache_local.stat().st_size > 0:
        try:
            conn = sqlite3.connect(str(cache_local))
            cursor = conn.cursor()
            
            # Проверяем структуру таблицы
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
            if cursor.fetchone():
                if username:
                    cursor.execute("SELECT uid, name FROM users WHERE name LIKE ?", (f'%{username}%',))
                    result = cursor.fetchone()
                    if result:
                        user_id, extracted_username = result
                        print(f"Найден пользователь: {extracted_username} (ID: {user_id})")
                
                if not user_id:
                    cursor.execute("SELECT uid, name FROM users ORDER BY uid DESC LIMIT 1")
                    result = cursor.fetchone()
                    if result:
                        user_id, extracted_username = result
                        print(f"Последний пользователь: {extracted_username} (ID: {user_id})")
            
            # Если users нет, пробуем другие таблицы
            if user_id is None:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = cursor.fetchall()
                for table in tables:
                    table_name = table[0]
                    try:
                        cursor.execute(f"PRAGMA table_info({table_name})")
                        columns = [col[1] for col in cursor.fetchall()]
                        if 'uid' in columns or 'user_id' in columns:
                            id_col = 'uid' if 'uid' in columns else 'user_id'
                            cursor.execute(f"SELECT {id_col} FROM {table_name} ORDER BY rowid DESC LIMIT 1")
                            result = cursor.fetchone()
                            if result and result[0] and result[0] > 10000:
                                user_id = result[0]
                                print(f"Найден user_id={user_id} из таблицы {table_name}")
                                break
                    except:
                        continue
            
            conn.close()
        except Exception as e:
            print(f"Ошибка чтения cache4.db: {e}")
    
    # Если user_id не найден, пробуем извлечь из tgnet.dat
    if user_id is None:
        for offset in [0x08, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40]:
            if len(data) > offset + 8:
                try:
                    candidate = struct.unpack('<Q', data[offset:offset+8])[0]
                    if candidate > 100000:  
                        user_id = candidate
                        print(f"Найден возможный user_id={user_id} из tgnet.dat на смещении 0x{offset:x}")
                        break
                except:
                    continue
    
    # Формируем результат
    session_data = {
        'phone': phone,
        'user_id': user_id,
        'dc_id': dc_id,
        'auth_key': auth_key.hex(),
        'username': extracted_username or username,
        'extracted_at': datetime.now().isoformat(),
        'tgnet_size': len(data),
        'auth_key_offset': auth_key_start,
        'key_file': str(key_file)
    }
    
    # Сохраняем
    json_file = SESSIONS_DIR / f"{phone}.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(session_data, f, indent=2, ensure_ascii=False)
    
    print(f"Сессия сохранена: {json_file}")
    print(f"   - user_id: {user_id}")
    print(f"   - dc_id: {dc_id}")
    print(f"   - username: {extracted_username or username}")
    
    # Очистка временных файлов
    tgnet_local.unlink(missing_ok=True)
    if cache_local.exists():
        cache_local.unlink(missing_ok=True)
    
    return session_data

# КЛАСС ТЕСТИРОВАНИЯ СЕССИИ 

class SessionTester:
    """Класс для тестирования извлеченной сессии"""
    
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.debug_log = []
    
    def log(self, message: str, level: str = "INFO"):
        print(message)
        self.debug_log.append(message)
    
    def test_session(self, auth_key_hex: str, dc_id: int, expected_user_id: int = None, phone: str = None) -> Dict[str, Any]:
        """Тестирование сессии"""
        self.debug_log = []
        
        self.log("\nТЕСТИРОВАНИЕ ИЗВЛЕЧЕННОЙ СЕССИИ")
        
        try:
            auth_key_bytes = bytes.fromhex(auth_key_hex)
            self.log(f"Размер ключа: {len(auth_key_bytes)} байт")
            
            if len(auth_key_bytes) != 256:
                self.log(f"Нестандартный размер! Ожидается 256 байт")
            
            auth_key_obj = AuthKey(auth_key_bytes)
            auth_key_id = format(auth_key_obj.key_id, '016x')
            self.log(f"AuthKey ID: {auth_key_id}")
            
        except Exception as e:
            self.log(f"Ошибка ключа: {e}")
            return {"success": False, "error": str(e), "debug_log": self.debug_log}
        
        # Пробуем подключиться к указанному DC
        dc_ips = {
            1: "149.154.175.50",
            2: "149.154.167.50", 
            3: "149.154.175.100",
            4: "149.154.167.91",
            5: "149.154.171.5"
        }
        
        ip = dc_ips.get(dc_id, "149.154.167.50")
        
        session = MemorySession()
        session.set_dc(dc_id, ip, 443)
        
        client = TelegramClient(session, TELEGRAM_API_ID, TELEGRAM_API_HASH)
        client.session.auth_key = auth_key_obj
        
        try:
            self.log(f"\nПодключение к DC{dc_id} ({ip})...")
            
            # Пробуем подключиться с таймаутом
            connect_task = asyncio.wait_for(client.connect(), timeout=15.0)
            self.loop.run_until_complete(connect_task)
            
            self.log("Подключено")
            
            # Проверяем авторизацию
            auth_task = asyncio.wait_for(client.is_user_authorized(), timeout=10.0)
            is_auth = self.loop.run_until_complete(auth_task)
            
            if is_auth:
                self.log("Клиент авторизован")
                
                # Получаем информацию о пользователе
                me_task = asyncio.wait_for(client.get_me(), timeout=10.0)
                me = self.loop.run_until_complete(me_task)
                
                self.log(f"User ID: {me.id}")
                if me.username:
                    self.log(f"Username: @{me.username}")
                if me.phone:
                    self.log(f"Phone: {me.phone}")
                
                self.loop.run_until_complete(client.disconnect())
                
                return {
                    "success": True, 
                    "user_id": me.id,
                    "username": me.username,
                    "phone": me.phone,
                    "debug_log": self.debug_log
                }
            else:
                self.log("Клиент не авторизован")
                return {"success": False, "error": "Не авторизован", "debug_log": self.debug_log}
                
        except asyncio.TimeoutError:
            self.log("Таймаут подключения")
            return {"success": False, "error": "Timeout", "debug_log": self.debug_log}
        except Exception as e:
            self.log(f"Ошибка: {e}")
            return {"success": False, "error": str(e), "debug_log": self.debug_log}
        finally:
            try:
                self.loop.run_until_complete(client.disconnect())
            except:
                pass
    
    def cleanup(self):
        self.loop.close()

# API ЭНДПОИНТЫ 

@api.route('/status')
class Status(Resource):
    def get(self):
        """Проверка статуса подключения к Android"""
        if not check_adb():
            return {'error': 'Android не подключен'}, 503
        
        sessions = list(SESSIONS_DIR.glob("*.json"))
        
        return {
            'status': 'ok',
            'android_connected': True,
            'telegram_installed': telegram_installed(),
            'telegram_authorized': is_authorized(),
            'sessions_count': len(sessions),
            'sessions': [f.stem for f in sessions]
        }

@api.route('/auth/start')
class AuthStart(Resource):
    @api.expect(auth_start_model)
    def post(self):
        """Начать процесс авторизации (очистить данные и запустить Telegram)"""
        data = request.json
        phone = data.get('phone')
        if not phone:
            return {'error': 'Укажите номер телефона'}, 400
        
        clear_telegram()
        launch_telegram()
        return {
            'status': 'waiting_for_code', 
            'phone': phone, 
            'message': 'Telegram запущен. Введите номер и код вручную через scrcpy'
        }

@api.route('/auth/verify')
class AuthVerify(Resource):
    @api.expect(auth_verify_model)
    def post(self):
        """Проверить, завершена ли авторизация"""
        data = request.json
        phone = data.get('phone')
        if not phone:
            return {'error': 'Укажите телефон'}, 400
        
        print(f"\nПроверка авторизации для {phone}...")
        
        if wait_for_authorization(30):
            return {'status': 'authorized', 'phone': phone}
        return {'status': 'waiting', 'phone': phone, 'message': 'Ожидание ввода кода в scrcpy'}

@api.route('/auth/extract')
class AuthExtract(Resource):
    @api.expect(extract_model)
    def post(self):
        """Извлечь данные сессии после авторизации"""
        data = request.json
        phone = data.get('phone')
        username = data.get('username')
        
        if not phone:
            return {'error': 'Укажите номер телефона'}, 400
        if not is_authorized():
            return {'error': 'Telegram не авторизован. Сначала выполните /auth/start и введите код'}, 400
        
        session = extract_session(phone, username)
        if not session:
            return {'error': 'Не удалось извлечь данные'}, 404
        
        return session

@api.route('/test/session/<string:phone>')
class TestSavedSession(Resource):
    def post(self, phone):
        """Протестировать сохраненную сессию"""
        
        session_file = SESSIONS_DIR / f"{phone}.json"
        if not session_file.exists():
            return {'error': f'Сессия для {phone} не найдена'}, 404
        
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
        except Exception as e:
            return {'error': f'Ошибка чтения файла: {e}'}, 500
        
        # Получаем ключ из данных сессии
        auth_key = session_data.get('auth_key')
        if not auth_key:
            # Ищем ключ в отдельном файле
            key_file = session_data.get('key_file')
            if key_file and Path(key_file).exists():
                with open(key_file, 'r') as f:
                    auth_key = f.read().strip()
            else:
                # Ищем по шаблону
                key_files = list(SESSIONS_DIR.glob(f"key_{phone}_*.key"))
                if key_files:
                    with open(key_files[0], 'r') as f:
                        auth_key = f.read().strip()
        
        if not auth_key:
            return {'error': 'Файл с ключом не найден'}, 404
        
        tester = SessionTester()
        result = tester.test_session(
            auth_key, 
            session_data.get('dc_id', 2),
            session_data.get('user_id'),
            phone
        )
        tester.cleanup()
        
        return result

@api.route('/sessions')
class SessionsList(Resource):
    def get(self):
        """Список всех сессий"""
        sessions = []
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    sessions.append({
                        'phone': data.get('phone'),
                        'user_id': data.get('user_id'),
                        'username': data.get('username'),
                        'extracted_at': data.get('extracted_at')
                    })
            except Exception as e:
                print(f"Ошибка чтения {f}: {e}")
                continue
        
        return {'sessions': sessions}

@api.route('/session/<string:phone>')
class SessionResource(Resource):
    def get(self, phone):
        """Получить конкретную сессию"""
        file = SESSIONS_DIR / f"{phone}.json"
        if not file.exists():
            return {'error': 'Сессия не найдена'}, 404
        
        with open(file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def delete(self, phone):
        """Удалить сессию"""
        file = SESSIONS_DIR / f"{phone}.json"
        if file.exists():
            # Удаляем также ключевой файл
            key_files = list(SESSIONS_DIR.glob(f"key_{phone}_*.key"))
            for kf in key_files:
                kf.unlink()
            file.unlink()
            return {'message': f'Сессия {phone} удалена'}
        return {'error': 'Сессия не найдена'}, 404

@api.route('/diagnose/network')
class DiagnoseNetwork(Resource):
    def get(self):
        """Диагностика сетевого подключения"""
        results = {}
        
        # Проверяем доступность дата-центров Telegram
        dcs = {
            1: "149.154.175.50",
            2: "149.154.167.50", 
            3: "149.154.175.100",
            4: "149.154.167.91",
            5: "149.154.171.5"
        }
        
        for dc_id, ip in dcs.items():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                start = time.time()
                result = sock.connect_ex((ip, 443))
                elapsed = time.time() - start
                
                results[f"DC{dc_id}"] = {
                    "ip": ip,
                    "reachable": result == 0,
                    "response_time_ms": round(elapsed * 1000, 2) if result == 0 else None
                }
                sock.close()
            except Exception as e:
                results[f"DC{dc_id}"] = {"ip": ip, "reachable": False, "error": str(e)}
        
        return results

# ИНИЦИАЛИЗАЦИЯ ПРИ ЗАПУСКЕ

print("\n")
print("ИНИЦИАЛИЗАЦИЯ")

# Проверяем и настраиваем инфраструктуру
infra_manager = InfrastructureManager()
if not infra_manager.setup_all():
    print("\nНекоторые компоненты не настроены, но микросервис продолжит работу")
    print("   Проверьте статус через /api/status")
else:
    print("\nИнфраструктура проверена и настроена")

print(f"\nПапка для сессий: {SESSIONS_DIR.absolute()}")
print(f"Swagger UI: http://localhost:5000/swagger/")
print(f"Для ручного ввода: adb connect localhost:5555 && scrcpy -s localhost:5555")



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
