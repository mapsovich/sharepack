"""Russian and English strings used by launcher.py (errors, install status, Java labels).
UI strings live in ui/i18n.js.

t("key", name=value) returns the string in the current language."""
import ctypes

LANGS = ("ru", "en")
LANG = "ru"

# key: (Russian, English)
STRINGS = {
    # download and install
    "stall_warning": (
        "загрузка не продвигается уже {seconds} с. Возможно, соединение медленное или нестабильное. "
        "Перезапускать лаунчер не нужно: при обрыве он переподключается сам. Если загрузка стоит дольше "
        "пары минут, проверьте подключение к интернету или отключите VPN",
        "the download hasn't progressed for {seconds} s. The connection may be slow or unstable. "
        "There is no need to restart the launcher: it reconnects on its own. If the download is stuck for more "
        "than a couple of minutes, check your internet connection or turn off your VPN"),
    "download_resumed": ("загрузка продолжилась", "download resumed"),
    "net_retry": ("соединение прервано, повторная попытка ({attempt})...",
                  "connection lost, retrying (attempt {attempt})..."),
    "installing_java": ("установка Java...", "installing Java..."),
    "installing_temurin": ("загрузка Java {major} (Eclipse Temurin)...", "downloading Java {major} (Eclipse Temurin)..."),
    "mirror_on": ("{host} недоступен, загрузка через зеркало BMCLAPI", "{host} is unreachable, downloading from the BMCLAPI mirror"),
    "using_java": ("используется уже установленная {label}", "using the installed {label}"),
    "installing_mc": ("установка Minecraft {mc}...", "installing Minecraft {mc}..."),
    "installing_loader": ("установка {loader} {version}...", "installing {loader} {version}..."),
    "downloading_files": ("загрузка файлов сборки: {count}", "downloading pack files: {count}"),
    "getting_pack": ("получение сборки...", "fetching the pack..."),
    "wiping": ("удаление старых файлов сборки...", "removing old pack files..."),
    "redownloaded": ("сборка перекачана", "pack redownloaded"),
    "launching": ("запуск игры...", "launching the game..."),
    "game_closed": ("игра закрыта", "game closed"),
    "exit_code": ("код {code}", "exit code {code}"),
    "unsafe_path": ("пропущен недопустимый путь из сборки: {path}", "skipped an unsafe path from the pack: {path}"),

    # short errors (button text)
    "play": ("играть", "play"),
    "e_bad_code": ("неверный код", "invalid code"),
    "e_code_not_found": ("код не найден", "code not found"),
    "e_server_down": ("сервер сборки недоступен", "pack server unreachable"),
    "e_pack": ("ошибка в сборке", "pack error"),
    "e_loader_install": ("ошибка установки {loader}", "{loader} install failed"),
    "e_download": ("не удалось скачать игру", "couldn't download the game"),
    "e_java_missing": ("java не найдена", "java not found"),
    "e_java_old": ("нужна java {need}", "java {need} required"),
    "e_dir": ("папка профилей недоступна", "profiles folder unavailable"),
    "e_ram": ("не хватит ОЗУ", "not enough RAM"),
    "e_redownload": ("не удалось перекачать", "redownload failed"),
    "e_crashed": ("игра вылетела", "game crashed"),
    "e_launch": ("ошибка запуска", "launch error"),
    "e_tampered": ("данные повреждены", "data corrupted"),

    # details (console)
    "d_bad_code": ("код не распознан. Проверьте, что он скопирован полностью",
                   "the code is not recognized. Make sure it was copied in full"),
    "d_code_not_found": ("сервер сборки работает, но этот код ему неизвестен. Возможно, владелец сменил код",
                         "the pack server is running but doesn't know this code. The owner may have changed it"),
    "d_server_down": ("сервер выключен или порт раздачи закрыт ({host}:{port})",
                      "the server is off or the pack port is closed ({host}:{port})"),
    "d_server_lost": ("соединение с сервером сборки прервано: {error}", "lost connection to the pack server: {error}"),
    "d_pack_server_failed": ("сервер сборки не смог составить список файлов. Подробности в консоли сервера",
                             "the pack server couldn't build the file list. See the server console for details"),
    "d_bad_signature": ("ответ сервера сборки не прошёл проверку подписи. Его могли изменить по пути (например, "
                        "в общественной сети Wi-Fi). Попробуйте другую сеть или обратитесь к владельцу сервера",
                        "the pack server response failed the signature check. It may have been modified on the way "
                        "(for example on public Wi-Fi). Try another network or contact the server owner"),
    "d_bad_file": ("файл {path} не совпадает с файлом на сервере. Возможно, владелец изменил его во время загрузки "
                   "или файл был изменён по пути. Нажмите «играть» ещё раз",
                   "the file {path} doesn't match the server copy. The owner may have changed it during the download, "
                   "or it was modified on the way. Press \"play\" again"),
    "d_pack_bad_json": ("файл sharepack/pack.json на сервере содержит ошибку: это не JSON",
                        "sharepack/pack.json on the server is not valid JSON"),
    "d_loader_install": ("установщик {loader} завершился с ошибкой на шаге {task}: {error}",
                         "the {loader} installer failed at step {task}: {error}"),
    "loader_fallback": ("загрузчика", "mod loader"),
    "mojang_servers": ("серверами Mojang", "Mojang servers"),
    "d_download": ("соединение с {host} постоянно прерывается. Проверьте подключение к интернету и нажмите "
                   "«играть» ещё раз, скачанные файлы сохранятся",
                   "the connection to {host} keeps dropping. Check your internet connection and press "
                   "\"play\" again; downloaded files are kept"),
    "d_no_mc_version": ("в pack.json не указана версия Minecraft (поле minecraft)",
                        "pack.json doesn't specify the Minecraft version (the minecraft field)"),
    "d_mc_auto": ("сервер не смог определить версию Minecraft. Укажите её в поле minecraft в sharepack/pack.json",
                  "the server couldn't detect the Minecraft version. Set the minecraft field in sharepack/pack.json"),
    "d_loader_auto": ("сервер не смог определить загрузчик модов. Укажите его в поле loader в sharepack/pack.json",
                      "the server couldn't detect the mod loader. Set the loader field in sharepack/pack.json"),
    "d_unknown_loader": ("в pack.json указан неизвестный загрузчик «{loader}». Поддерживаются: {known}",
                         "pack.json has an unknown mod loader \"{loader}\". Supported: {known}"),
    "d_bad_memory": ("в pack.json неверное значение memory: «{value}»", "pack.json has an invalid memory value: \"{value}\""),
    "d_mc_missing": ("версии Minecraft {mc} не существует. Проверьте поле minecraft в pack.json",
                     "Minecraft {mc} doesn't exist. Check the minecraft field in pack.json"),
    "d_loader_no_mc": ("{name} не поддерживает Minecraft {mc}", "{name} doesn't support Minecraft {mc}"),
    "d_loader_version_missing": ("версии {name} {version} для Minecraft {mc} не существует",
                                 "{name} {version} doesn't exist for Minecraft {mc}"),
    "d_java_missing": ("Java из настроек не найдена: {java}. Выберите другую или «по умолчанию»",
                       "the Java set in the settings was not found: {java}. Choose another one or \"default\""),
    "d_java_old": ("для Minecraft {mc} нужна Java {need} или новее, в настройках выбрана {label}. "
                   "Выберите другую или «по умолчанию»",
                   "Minecraft {mc} needs Java {need} or newer, the settings use {label}. "
                   "Choose another one or \"default\""),
    "d_dir": ("нет доступа на запись в папку профилей {folder}: {error}",
              "can't write to the profiles folder {folder}: {error}"),
    "d_ram": ("игре выделено {memory} МБ, а в системе всего {total} МБ. Уменьшите значение «выделено ОЗУ» в настройках",
              "the game is given {memory} MB, but the system has only {total} MB. "
              "Lower \"allocated RAM\" in the settings"),
    "d_redownload": ("не удалось удалить старые файлы. Возможно, игра ещё запущена ({error})",
                     "couldn't remove the old files. The game may still be running ({error})"),
    "d_crashed": ("игра завершилась с ошибкой (код {code}). Лог: {log}",
                  "the game exited with an error (code {code}). Log: {log}"),

    # pack info
    "core_latest": ("последняя", "latest"),

    # settings and pack list
    "nick_invalid": ("никнейм: 3–16 символов, латиница, цифры и _", "nickname: 3–16 characters, Latin letters, digits and _"),
    "java_from_launcher": ("Java {major} (из Minecraft Launcher)", "Java {major} (from Minecraft Launcher)"),
    "all_files": ("Все файлы (*.*)", "All files (*.*)"),
    "ram_invalid": ("ОЗУ: укажите размер, например 4096 или 4G", "RAM: enter a size, e.g. 4096 or 4G"),
    "java_invalid": ("Java: выбранный файл не является java.exe", "Java: the selected file is not java.exe"),
    "dir_create_failed": ("папка профилей: не удалось создать папку", "profiles folder: couldn't create the folder"),
    "bad_pack_code": ("неверный код сборки", "invalid pack code"),
    "delete_failed": ("не удалось удалить файлы сборки. Возможно, игра ещё запущена ({error})",
                      "couldn't delete the pack files. The game may still be running ({error})"),

    # no WebView2
    "webview2_missing": ("Для работы лаунчера нужен Microsoft Edge WebView2 (бесплатный компонент Windows).\n\n"
                         "Открыть страницу загрузки?",
                         "The launcher needs Microsoft Edge WebView2 (a free Windows component).\n\n"
                         "Open the download page?"),
}


def t(key, **values):
    text = STRINGS[key][LANGS.index(LANG)]
    return text.format(**values) if values else text


def set_lang(lang):
    global LANG
    LANG = lang if lang in LANGS else LANG


def system_lang():
    """Russian for ru/uk/be/kk Windows UI languages, English otherwise."""
    try:
        primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
    except (AttributeError, OSError):
        return "en"
    return "ru" if primary in (0x19, 0x22, 0x23, 0x3F) else "en"
