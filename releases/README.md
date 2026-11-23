# Готовые сборки

Здесь лежат собранные артефакты. Сами файлы **не входят в git-репозиторий**
(см. `.gitignore`) — они прикладываются к разделу
[Releases](../../releases) на GitHub.

| Файл | Что это |
|---|---|
| `FreeLLMRouter-Setup-<версия>.exe` (24 МБ) | **Установщик: скачать → двойной клик → готово.** Встроенный Python 3.12 и зависимости, интернет при установке не нужен. Прав администратора не требует |
| `FreeLLMRouter-win64-portable.zip` (20 МБ) | Без установки: распаковать и запустить `install.cmd` |
| `FreeLLMRouter-win64.zip` (130 КБ) | Тонкий вариант: Python поставится сам |
| `SHA256SUMS.txt` | Контрольные суммы |

Установка и разбор SmartScreen: `windows/WIN-INSTALL.md`.

## Собрать самому

```bash
./build_setup.sh       # → releases/FreeLLMRouter-Setup-<версия>.exe (нужен makensis)
./build_portable.sh    # → releases/FreeLLMRouter-win64-portable.zip
./build_zip.sh         # → releases/FreeLLMRouter-win64.zip
```

Сборка требует интернет только в первый раз: встроенный Python и колёса
зависимостей кешируются в `build/`.
