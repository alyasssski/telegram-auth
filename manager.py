import os
import time
import json
import sqlite3
import subprocess
import select
import asyncio
import sys
import shutil
import re
import functools
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from flask import Flask, request
from flask_restx import Api, Resource, fields
from telethon import TelegramClient
from telethon.sessions import MemorySession

print = functools.partial(print, flush=True)

ANDROID_SESSION_AVAILABLE = False

try:
    from AndroidTelePorter import AndroidSession
    ANDROID_SESSION_AVAILABLE = True
except ImportError as e:
    ANDROID_SESSION_AVAILABLE = False
    print("AndroidTelePorter не установлен или ошибка импорта")
    print(f"   Детали ошибки: {e}")

SESSIONS_DIR = Path("./sessions")
SESSIONS_DIR.mkdir(exist_ok=True)
ADB_DEVICE = "localhost:5555"

app = Flask(__name__)
api = Api(
    app,
    version='1.0',
    title='Telegram Auth',
    description='Извлечение сессий Telegram через Android контейнер',
    doc='/swagger/',
    prefix='/api'
)

auth_start_model = api.model('AuthStart', {
    'phone': fields.String(required=True, description='Номер телефона в формате +7')
})

extract_model = api.model('Extract', {
    'phone': fields.String(required=True, description='Номер телефона в формате +7')
})

reauthorize_model = api.model('Reauthorize', {
    'api_id': fields.Integer(required=True, description='Telegram API ID'),
    'api_hash': fields.String(required=True, description='Telegram API Hash')
})

class InfrastructureManager:

    def __init__(self):
        self.docker_available = self._check_docker()

    def _check_docker(self) -> bool:
        try:
            result = subprocess.run(["docker", "--version"], capture_output=True, text=True)
            return result.returncode == 0
        except:
            return False

    def check_android_container(self) -> Dict[str, Any]:
        result = {
            'running': False,
            'container_name': None,
            'container_id': None,
            'action_taken': None
        }

       

        if not self.docker_available:
            result['running'] = True
            result['container_name'] = 'android'
            return result

        try:
            ps_result = subprocess.run(
                "docker ps --format '{{.Names}}' | grep -E 'redroid|android'",
                shell=True, capture_output=True, text=True
            )

            containers = ps_result.stdout.strip().split('\n')
            for container in containers:
                if container and ('redroid' in container or 'android' in container):
                    result['running'] = True
                    result['container_name'] = container
                    print(f"Найден запущенный контейнер: {container}", flush=True)

                    id_result = subprocess.run(
                        f"docker ps --filter name={container} --format '{{{{.ID}}}}'",
                        shell=True, capture_output=True, text=True
                    )
                    result['container_id'] = id_result.stdout.strip()
                    break

            if not result['running']:
                print("Запущенный Android контейнер не найден", flush=True)
                print("Запуск нового контейнера...", flush=True)

                self._start_android_container()
                result['running'] = True
                result['container_name'] = 'redroid12'
                result['action_taken'] = 'started_new_container'

        except Exception as e:
            print(f"Ошибка при проверке: {e}", flush=True)
            result['error'] = str(e)

        return result

    def _start_android_container(self):
        print("\nЗапуск Android контейнера:", flush=True)

        home = os.path.expanduser("~")
        data_dir = os.path.join(home, "data")
        os.makedirs(data_dir, exist_ok=True)

        cmd = (
            "docker run -itd --rm --privileged "
            "--pull always "
            "--name redroid12 "
            f"-v {data_dir}:/data "
            "-p 5555:5555 "
            "redroid/redroid:12.0.0_64only-latest"
        )

        print(f"  Выполнение команды: {cmd}", flush=True)
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            print("Контейнер запущен", flush=True)
            time.sleep(30)
        else:
            print(f"Ошибка: {result.stderr}", flush=True)
            raise Exception("Не удалось запустить Android контейнер")

    def check_adb_connection(self) -> bool:
        print("\nПРОВЕРКА ADB ПОДКЛЮЧЕНИЯ:", flush=True)

        import sys
        current_device = sys.modules['__main__'].ADB_DEVICE

        connect_result = subprocess.run(
            f"adb connect {current_device}",
            shell=True, capture_output=True, text=True
        )

        if "connected" in connect_result.stdout:
            print(f"ADB подключен к {current_device}", flush=True)
            return True

        print(f"Не удалось подключиться к {current_device}", flush=True)

        alt_result = subprocess.run(
            "adb connect localhost:5555",
            shell=True, capture_output=True, text=True
        )

        if "connected" in alt_result.stdout:
            print("ADB подключен к localhost:5555", flush=True)
            sys.modules['__main__'].ADB_DEVICE = "localhost:5555"
            globals()['ADB_DEVICE'] = "localhost:5555"
            return True

        print("Не удалось подключиться к ADB", flush=True)
        return False

    def check_telegram_installed(self) -> bool:
        print("\nПРОВЕРКА TELEGRAM:", flush=True)

        import sys
        current_device = sys.modules['__main__'].ADB_DEVICE

        result = subprocess.run(
            f"adb -s {current_device} shell pm list packages | grep org.telegram.messenger",
            shell=True, capture_output=True, text=True
        )

        if result.stdout.strip():
            print("Telegram уже установлен", flush=True)
            return True

        print("Установка Telegram...", flush=True)

        apk_path = "/tmp/telegram.apk"
        if not Path(apk_path).exists():
            print("Скачивание Telegram APK...", flush=True)
            subprocess.run(
                "wget https://telegram.org/dl/android/apk -O /tmp/telegram.apk",
                shell=True, check=True
            )

        install_result = subprocess.run(
            f"adb -s {current_device} install {apk_path}",
            shell=True, capture_output=True, text=True
        )

        if install_result.returncode == 0:
            print("Telegram установлен", flush=True)
            return True
        else:
            print(f"Ошибка установки: {install_result.stderr}", flush=True)
            reinstall_result = subprocess.run(
                f"adb -s {current_device} install -r {apk_path}",
                shell=True, capture_output=True, text=True
            )
            if reinstall_result.returncode == 0:
                print("Telegram переустановлен", flush=True)
                return True
            return False

    def setup_all(self) -> bool:
       

        container_info = self.check_android_container()
        if not container_info['running']:
            print("Не удалось запустить Android контейнер", flush=True)
            return False

        if not self.check_adb_connection():
            print("Не удалось подключиться по ADB", flush=True)
            return False

        time.sleep(5)

        if not self.check_telegram_installed():
            print("Не удалось установить Telegram", flush=True)
            return False

       

        return True

def adb(command: str) -> tuple[bool, str]:
    full_cmd = f"adb -s {ADB_DEVICE} {command}"
    try:
        result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout.strip()
    except Exception as e:
        return False, str(e)

def adb_root_command(commands: List[str], timeout: int = 30) -> tuple[bool, str]:
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
        print(f"Ошибка в adb_root_command: {e}", flush=True)
        return False, ""
    finally:
        try:
            process.terminate()
        except:
            pass

def check_adb() -> bool:
    success, output = adb("get-state")
    return success and "device" in output

def telegram_installed() -> bool:
    success, output = adb("shell pm list packages | grep org.telegram.messenger")
    return bool(output.strip())

def clear_telegram() -> None:
    print("Очистка данных Telegram...", flush=True)
    adb("shell pm clear org.telegram.messenger.web")
    time.sleep(2)

def launch_telegram() -> None:
    print("Запуск Telegram...", flush=True)
    adb("shell am start -n org.telegram.messenger.web/org.telegram.ui.LaunchActivity")
    print("\n", flush=True)
    print("Откройте scrcpy в другом окне:", flush=True)
    print("   scrcpy -s localhost:5555", flush=True)
    print("\n", flush=True)

def is_authorized() -> bool:
    try:
        print("Проверка авторизации...", flush=True)
        
        commands = [
            'sqlite3 /data/data/org.telegram.messenger.web/files/cache4.db "SELECT COUNT(*) FROM users;"'
        ]
        
        success, output = adb_root_command(commands, timeout=10)

        if not success or not output:
            print("Нет вывода от sqlite", flush=True)
            return False

        match = re.search(r'\b\d+\b', output)

        if match:
            count = int(match.group())
            if count > 0:
                print(f"Пользователь авторизован! Записей в users: {count}", flush=True)
                return True

        print("Пользователь не авторизован (таблица users пуста)", flush=True)
        return False
        
    except Exception as e:
        print(f"Ошибка при проверке авторизации: {e}", flush=True)
        return False

def pull_file(remote: str, local: str) -> bool:
    try:
        print(f"Копирование {remote}...", flush=True)

        mkdir_commands = [
            "mkdir -p /sdcard/telegram_session",
            "chmod 777 /sdcard/telegram_session"
        ]
        adb_root_command(mkdir_commands, timeout=5)
        
        filename = remote.split('/')[-1]
        copy_commands = [
            f"cp {remote} /sdcard/telegram_session/{filename}",
            f"chmod 644 /sdcard/telegram_session/{filename}"
        ]
        
        success, output = adb_root_command(copy_commands, timeout=15)
        
        if not success:
            print("Не удалось скопировать файл на sdcard", flush=True)
            return False

        time.sleep(1)

        pull_cmd = f"adb -s {ADB_DEVICE} pull /sdcard/telegram_session/{filename} {local}"
        print(f"  Выполнение команды: {pull_cmd}", flush=True)
        result = subprocess.run(pull_cmd, shell=True, capture_output=True, text=True, timeout=30)

        cleanup_commands = [f"rm /sdcard/telegram_session/{filename}"]
        adb_root_command(cleanup_commands, timeout=5)

        if result.returncode == 0 and Path(local).exists():
            local_size = Path(local).stat().st_size
            print(f"Файл скопирован: {local} ({local_size} байт)", flush=True)
            return True
        else:
            print(f"Не удалось скопировать файл: {result.stderr}", flush=True)
            return False
            
    except Exception as e:
        print(f"Ошибка при копировании: {e}", flush=True)
        return False

def is_session_valid(session_file: Path, api_id: int, api_hash: str) -> bool:
    try:
        async def check():
            client = TelegramClient(str(session_file), api_id, api_hash)
            await client.connect()
            is_valid = await client.is_user_authorized()
            await client.disconnect()
            return is_valid
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(check())
        loop.close()
        return result
    except Exception as e:
        print(f"Ошибка проверки сессии: {e}", flush=True)
        return False

def pull_tgnet_and_userconfig(phone: str) -> Tuple[Optional[Path], Optional[Path]]:
    print(f"\nКОПИРОВАНИЕ ФАЙЛОВ ДЛЯ {phone}...", flush=True)

    if not is_authorized():
        print("Telegram не авторизован на Android", flush=True)
        return None, None

    tgnet_local = SESSIONS_DIR / f"tgnet_{phone}.dat"
    userconfig_local = SESSIONS_DIR / f"userconfing_{phone}.xml"

    print("Копирование tgnet.dat...", flush=True)
    if not pull_file(
        "/data/data/org.telegram.messenger.web/files/tgnet.dat",
        str(tgnet_local)
    ):
        print("Не удалось скопировать tgnet.dat", flush=True)
        return None, None

    print("Копирование userconfing.xml...", flush=True)
    if not pull_file(
        "/data/data/org.telegram.messenger.web/shared_prefs/userconfing.xml",
        str(userconfig_local)
    ):
        print("Не удалось скопировать userconfing.xml", flush=True)
        return None, None

    print(f"Файлы скопированы:", flush=True)
    print(f"   tgnet.dat: {tgnet_local} ({tgnet_local.stat().st_size} байт)", flush=True)
    print(f"   userconfing.xml: {userconfig_local} ({userconfig_local.stat().st_size} байт)", flush=True)

    return tgnet_local, userconfig_local

def extract_session_with_android_porter(phone: str) -> Optional[Dict[str, Any]]:
    print(f"\nИЗВЛЕЧЕНИЕ СЕССИИ ДЛЯ {phone}", flush=True)

    tgnet_path, userconfig_path = pull_tgnet_and_userconfig(phone)
    
    if not tgnet_path or not userconfig_path:
        print("Не удалось скопировать файлы", flush=True)
        return None

    print("Создание сессии через AndroidTelePorter...", flush=True)
    session = AndroidSession.from_tgnet(
        tgnet_path=str(tgnet_path),
        userconfig_path=str(userconfig_path)
    )
    print("Сессия успешно загружена!", flush=True)

    phone_session = SESSIONS_DIR / f"{phone}.session"
    session.to_telethon(str(phone_session))
    print(f"Сессия сохранена: {phone_session}", flush=True)

    auth_key_hex = None
    dc_id = None
    user_id = None
    extracted_username = None
    
    try:
        conn = sqlite3.connect(str(phone_session))
        cursor = conn.cursor()
        
        cursor.execute("SELECT dc_id, auth_key FROM sessions")
        session_row = cursor.fetchone()
        if session_row:
            dc_id = session_row[0]
            auth_key_blob = session_row[1]
            if auth_key_blob:
                auth_key_hex = auth_key_blob.hex()
        
        cursor.execute("SELECT id, username FROM entities WHERE id > 0 LIMIT 1")
        user_row = cursor.fetchone()
        if user_row:
            user_id = user_row[0]
            extracted_username = user_row[1]
        
        conn.close()
        
        print(f"ИЗВЛЕЧЕННЫЕ ДАННЫЕ:", flush=True)
        print(f"   DC ID: {dc_id}", flush=True)
        print(f"   User ID: {user_id}", flush=True)
        print(f"   Username: {extracted_username}", flush=True)
        if auth_key_hex:
            print(f"   Auth Key: {auth_key_hex[:50]}...", flush=True)
        
    except Exception as e:
        print(f"Ошибка чтения SQLite: {e}", flush=True)

    tgnet_path.unlink(missing_ok=True)
    userconfig_path.unlink(missing_ok=True)

    result = {
        'phone': phone,
        'auth_key': auth_key_hex,
        'dc_id': dc_id,
        'user_id': user_id if user_id else 0,
        'username': extracted_username,
        'message': 'Сессия успешно извлечена'
    }
    
    json_file = SESSIONS_DIR / f"{phone}.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump({
            'phone': phone,
            'user_id': user_id if user_id else 0,
            'username': extracted_username,
            'dc_id': dc_id,
            'auth_key': auth_key_hex,
            'extracted_at': datetime.now().isoformat()
        }, f, ensure_ascii=False, indent=2)
    
    return result

@api.route('/status')
class Status(Resource):
    def get(self):
        print("\nЗАПРОС СТАТУСА", flush=True)
        
        if not check_adb():
            print("Android не подключен", flush=True)
            return {'error': 'Android не подключен'}, 503

        sessions = list(SESSIONS_DIR.glob("*.json"))
        telethon_sessions = list(SESSIONS_DIR.glob("*.session"))
        telegram_authorized = is_authorized()

       
        print(f"Telegram авторизован: {'Yes' if telegram_authorized else 'No'}", flush=True)
        print(f"Сессий JSON: {len(sessions)}", flush=True)
        print(f"Сессий session: {len(telethon_sessions)}", flush=True)
        

        return {
            'status': 'ok',
            'android_connected': True,
            'telegram_installed': telegram_installed(),
            'telegram_authorized_on_android': telegram_authorized,
            'sessions_count': len(sessions),
            'sessions': [f.stem for f in sessions],
            'session_files': [f.stem for f in telethon_sessions]
        }

@api.route('/auth/start')
class AuthStart(Resource):
    @api.expect(auth_start_model)
    def post(self):
        data = request.json
        phone = data.get('phone')
        
        print(f"\nЗАПУСК АВТОРИЗАЦИИ ДЛЯ {phone}", flush=True)
        
        if not phone:
            return {'error': 'Укажите номер телефона'}, 400

        clear_telegram()
        launch_telegram()
      
        
        return {
            'status': 'waiting_for_code',
            'phone': phone,
            'message': 'Telegram запущен. Введите номер и код вручную через scrcpy'
        }

@api.route('/auth/extract-and-save')
class AuthExtractAndSave(Resource):
    @api.expect(extract_model)
    def post(self):
        data = request.json
        phone = data.get('phone')

        print(f"\nЗАПРОС НА ИЗВЛЕЧЕНИЕ СЕССИИ ДЛЯ {phone}", flush=True)
       

        if not phone:
            return {'error': 'Укажите номер телефона'}, 400

        if not ANDROID_SESSION_AVAILABLE:
            return {'error': 'AndroidTelePorter не доступен'}, 500

        if not is_authorized():
            return {'error': 'Telegram не авторизован на Android.'}, 400

        session = extract_session_with_android_porter(phone)
        if not session:
            return {'error': 'Не удалось извлечь данные'}, 404

        return session

@api.route('/auth/reauthorize/<string:phone>')
class Reauthorize(Resource):
    @api.expect(reauthorize_model)
    def post(self, phone):
        data = request.json
        api_id = data.get('api_id')
        api_hash = data.get('api_hash')
        
        print(f"\nПЕРЕАВТОРИЗАЦИЯ ДЛЯ {phone}", flush=True)
        
        
        if not api_id or not api_hash:
            return {'error': 'Укажите API ID и API Hash'}, 400
            
        session_file = SESSIONS_DIR / f"{phone}.session"

        if not session_file.exists():
            json_file = SESSIONS_DIR / f"{phone}.json"
            if json_file.exists():
                with open(json_file, 'r', encoding='utf-8') as f:
                    session_data = json.load(f)
                
                try:
                    temp_session = MemorySession()
                    
                    if session_data.get('dc_id') and session_data.get('auth_key'):
                        temp_session.set_dc(
                            session_data['dc_id'],
                            f"149.154.167.{50 + (session_data['dc_id']-1)*41}",
                            443
                        )
                        
                        auth_key_bytes = bytes.fromhex(session_data['auth_key'])
                        temp_session.auth_key = auth_key_bytes
                        
                        import pickle
                        with open(session_file, 'wb') as f:
                            session_dict = {
                                'dc_id': session_data['dc_id'],
                                'server_address': f"149.154.167.{50 + (session_data['dc_id']-1)*41}",
                                'port': 443,
                                'auth_key': auth_key_bytes,
                                'takeout_id': None,
                                'user_id': session_data.get('user_id')
                            }
                            pickle.dump(session_dict, f)
                        print(f"Session файл создан из JSON", flush=True)
                except Exception as e:
                    print(f"Ошибка создания session файла: {e}", flush=True)
                    return {'error': f'Не удалось создать session файл: {e}'}, 500
            else:
                print(f"Сессия для {phone} не найдена", flush=True)
                return {'error': f'Сессия для {phone} не найдена'}, 404

        print(f"Использование файла сессии: {session_file}", flush=True)
        print(f"API ID: {api_id}", flush=True)
        print(f"API Hash: {api_hash[:5]}...", flush=True)

        try:
            client = TelegramClient(str(session_file), api_id, api_hash)

            async def reauthorize():
                try:
                    print("Подключение к Telegram...", flush=True)
                    await client.connect()
                    print("Подключение установлено", flush=True)

                    if not await client.is_user_authorized():
                        print("Сессия не авторизована", flush=True)
                        return {"success": False, "error": "Сессия не авторизована"}

                    print("Получение информации о пользователе...", flush=True)
                    me = await client.get_me()
                    print("Авторизация успешна!", flush=True)
                    print(f"   ID: {me.id}", flush=True)
                    print(f"   Username: @{me.username}", flush=True)
                    print(f"   Phone: {me.phone}", flush=True)

                    await client.disconnect()
                    print("Отключение от Telegram", flush=True)
                    

                    return {
                        "success": True,
                        "user_id": me.id,
                        "username": me.username,
                        "phone": me.phone,
                        "message": "Авторизация успешна!"
                    }

                except Exception as e:
                    print(f"Ошибка: {e}", flush=True)
                    return {"success": False, "error": str(e)}

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(reauthorize())
            loop.close()

            return result

        except Exception as e:
            print(f"Ошибка: {e}", flush=True)
            return {"success": False, "error": str(e)}

@api.route('/sessions')
class SessionsList(Resource):
    def get(self):
        print("\nЗАПРОС СПИСКА СЕССИЙ", flush=True)
        
        
        sessions = []
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    sessions.append({
                        'phone': data.get('phone'),
                        'user_id': data.get('user_id'),
                        'username': data.get('username'),
                        'dc_id': data.get('dc_id'),
                        'extracted_at': data.get('extracted_at')
                    })
            except Exception as e:
                print(f"Ошибка чтения {f}: {e}", flush=True)
                continue

        print(f"Найдено сессий: {len(sessions)}", flush=True)
        
        
        return {'sessions': sessions}
    
    def delete(self):
        print("\nУДАЛЕНИЕ ВСЕХ СЕССИЙ", flush=True)
        
        
        try:
            deleted_count = 0
            
            for f in SESSIONS_DIR.glob("*.json"):
                f.unlink()
                deleted_count += 1
                print(f"  Удален: {f.name}", flush=True)
            
            for f in SESSIONS_DIR.glob("*.session"):
                f.unlink()
                deleted_count += 1
                print(f"  Удален: {f.name}", flush=True)
            
            for f in SESSIONS_DIR.glob("tgnet_*.dat"):
                f.unlink(missing_ok=True)
                deleted_count += 1
                print(f"  Удален: {f.name}", flush=True)
            
            for f in SESSIONS_DIR.glob("userconfing_*.xml"):
                f.unlink(missing_ok=True)
                deleted_count += 1
                print(f"  Удален: {f.name}", flush=True)
            
            print(f"\nУдалено файлов: {deleted_count}", flush=True)
            
            
            return {
                'message': f'Удалено {deleted_count} файлов',
                'deleted_count': deleted_count
            }
            
        except Exception as e:
            print(f"Ошибка при удалении: {e}", flush=True)
            return {'error': f'Ошибка при удалении: {e}'}, 500



print("\nПРОВЕРКА ЗАВИСИМОСТЕЙ:", flush=True)
if ANDROID_SESSION_AVAILABLE:
    print("AndroidTelePorter успешно импортирован", flush=True)
else:
    print("AndroidTelePorter не доступен", flush=True)


infra_manager = InfrastructureManager()
if not infra_manager.setup_all():
    print("\nНекоторые компоненты не настроены", flush=True)


print(f"\nПапка для сессий: {SESSIONS_DIR.absolute()}", flush=True)
print(f"Swagger UI: http://localhost:5000/swagger/", flush=True)
print(f"Для ручного ввода: scrcpy -s localhost:5555", flush=True)
print("\n", flush=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
