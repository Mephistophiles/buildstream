# bst-mc: BuildStream в Midnight Commander

Плагин extfs открывает BuildStream-проект как виртуальную файловую систему MC.
Требуются Python 3.10+, MC с extfs/tarfs, установленный `bst` и плагины проекта.
Граф и инспекция используют публичный CLI BuildStream и код соседнего `bst-tree`.

## Установка

Из корня этого репозитория, в окружении, где уже работает ваш `bst`:

```sh
python -m pip install ./contrib/bst-tree ./contrib/mcplugin

# Установка extfs для текущего пользователя, без sudo:
mc_data="${XDG_DATA_HOME:-$HOME/.local/share}/mc"
mkdir -p "$mc_data/extfs.d"
ln -s "$(command -v bstmc)" "$mc_data/extfs.d/bstmc"
```

Если используется virtualenv, активируйте его перед установкой и запуском MC.
Ссылка ведёт на entry point с Python этого окружения; `bst` должен оставаться
доступен в `PATH`. `ln` намеренно не заменяет уже существующий плагин.
Пути конкретной сборки MC можно проверить командой `mc --datadir-info`.
После установки перезапустите MC.

Открыть проект сразу в новой сессии MC:

```sh
bst-mc -C /path/to/project app.bst
bst-mc -C /path/to/project --option arch x86-64 app.bst tools.bst
```

Без списка элементов используются цели проекта по умолчанию, определяемые
`bst show`. Поддерживаются junction-имена (`sdk.bst:app.bst`). Все запросы
выполняются в strict mode с указанными опциями проекта.

Для открытия из уже запущенного MC создайте постоянную закладку:

```sh
bst-mc -C /path/to/project app.bst -o ~/project.bstmc
```

В командной строке MC выполните `cd ~/project.bstmc/bstmc://`.
Закладка — JSON с абсолютным путём проекта, целями и опциями, а не снимок графа.
Существующий файл команда не перезаписывает.

Чтобы открывать `.bstmc` клавишей Enter, через меню MC **Command → Edit extension
file** откройте пользовательский `mc.ext.ini` и добавьте секцию из
[`mc.ext.ini`](mc.ext.ini) **перед `[Default]`**, сохранив остальные ассоциации:

```ini
[buildstream-project]
Shell=.bstmc
Open=%cd %p/bstmc://
```

Это синтаксис современных версий MC (4.8.29+). Для старого `mc.ext`:

```text
shell/.bstmc
    Open=%cd %p/bstmc://
```

Ассоциация нужна только для Enter на закладке; `bst-mc` и прямой `cd` работают
без неё. Архивы `.tar` внутри проекта открываются стандартной ассоциацией MC.

## Навигация

```text
project.bstmc/bstmc://
  README.txt
  all/                       # весь граф
    tree.txt                 # дерево, F3
    targets/                 # ссылки на выбранные цели
    elements/                # индекс элементов
      app.bst/
        dependencies/        # ссылки с типом [build], [run], [build+run]
        reverse-dependencies/
        element.json         # имя, kind, ключ, provenance, наличие workspace
        paths.txt            # кратчайший путь от каждой подходящей цели
        source-info.txt
        build-commands.txt
        artifact-list.txt
        sources.tar
        artifact.tar
  build/                     # build-зависимости целей и их runtime-замыкание
  run/                       # runtime-замыкание целей
```

Enter открывает каталоги и ссылки зависимостей. `..` поднимает на уровень выше,
Alt-y возвращает в предыдущий каталог истории MC, Ctrl-s ищет по именам в панели.
Ссылки ведут в канонический каталог элемента: общие зависимости не дублируют
весь подграф. `tree.txt` разворачивает каждый узел один раз и помечает повторы
`[see above]`; циклы тоже завершаются. Типы рёбер, области графа, обратные
зависимости и кратчайшие пути используют ту же модель, что `bst-tree`.
Слеши и двоеточия в именах закодированы (`base%2Flib.bst`, `sdk.bst%3Aapp.bst`),
чтобы junction-имя оставалось одним пунктом; исходное имя видно в `element.json`.

Данные загружаются при обращении к соответствующему файлу:

| Файл | Действие |
| --- | --- |
| `source-info.txt` | F3: `bst show --format '%{source-info}'` |
| `build-commands.txt` | F3: resolved config, variables и environment из `bst show` |
| `artifact-list.txt` | F3: `bst artifact list-contents --long` |
| `sources.tar` | Enter: `bst source checkout --deps none --tar …` |
| `artifact.tar` | Enter: `bst artifact checkout --deps none --no-integrate --tar …` |

Внутри tar — обычное дерево файлов: Enter открывает каталог, F3 показывает
содержимое файла, F5 копирует его в другую панель. Доступны и бинарные файлы через
просмотрщик MC. Команды сборки — эффективная конфигурация, а не исходный YAML
или лог; набор команд зависит от kind элемента.

## Загрузка и ограничения

При открытии проекта загружается только граф. Просмотр списка артефакта не
выгружает его файлы. Открытие tar экспортирует **весь выбранный элемент** во
временный архив, что может потребовать времени и места. Source checkout может
скачивать отсутствующие исходники; artifact checkout может скачивать артефакт
из настроенных remotes. Сборка, track и integration-команды не запускаются.
Без доступного артефакта checkout завершается ошибкой. Открытый workspace
обрабатывается по правилам `bst source checkout`; это не обязательно исходники
уже собранного артефакта.

VFS доступна только для чтения: изменения, удаления и загрузка файлов обратно
не поддерживаются. Не редактируйте вложенный tar: MC не сможет сохранить его
в проект. Промежуточный экспорт удаляется после команды, вложенным архивом
управляет временный VFS-кеш MC. При нормальном завершении MC очищает его.
При отмене команды временный экспорт очищается; SIGKILL/аварийное завершение
может оставить файлы в системном временном каталоге.

MC кеширует листинг и уже открытые файлы; размеры ещё не загруженных
виртуальных файлов показываются как 0. Для обновления выйдите из VFS и
освободите её через список активных VFS MC либо перезапустите MC.
Одного Ctrl-r может быть недостаточно. Запросы являются отдельными живыми
операциями: изменённый между ними проект может дать отличающийся результат.
Ошибки `bst` выводятся в stderr и в диалоге ошибки extfs MC.

## Проверка

```sh
python -m pip install -e './contrib/mcplugin[test]' -e ./contrib/bst-tree
python -m pytest -c contrib/mcplugin/pyproject.toml contrib/mcplugin/tests
# Интеграция с настоящим bst/buildbox-casd:
BST_MC_TEST_LIVE=1 python -m pytest -c contrib/mcplugin/pyproject.toml contrib/mcplugin/tests

# Проверка протокола без запуска MC:
bstmc list ~/project.bstmc
bstmc copyout ~/project.bstmc all/elements/app.bst/build-commands.txt /tmp/commands.txt
```

Протокол: [официальный README extfs MC](https://github.com/MidnightCommander/mc/blob/master/src/vfs/extfs/helpers/README).

Проверено с MC 4.8.33, BuildStream 2.8.0 и Python 3.14 на macOS ARM64:
переходы через `targets/` и `dependencies/`, F3 для build commands,
Enter на `sources.tar` и F3 для файла с пробелами в имени.
Для live-тестов на macOS может потребоваться `ulimit -n 4096`;
`buildbox-casd` доступен в Homebrew-пакете `recc`.
