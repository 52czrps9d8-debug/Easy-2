# Easy-2: OpenMontage + chad (chadgpt.ru)

Модуль для [OpenMontage](https://github.com/calesthio/OpenMontage), который
добавляет генерацию картинок и (экспериментально) видео через API
[chad / ChadGPT](https://api.chadgpt.ru) — с оплатой в рублях с баланса chad.

Сам OpenMontage в этот репозиторий не копируется: `install.sh` скачивает
проверенную версию и докладывает в неё файлы из `overlay/`.

## Установка

```bash
./install.sh                       # OpenMontage будет в ../OpenMontage
OPENMONTAGE_DIR=~/om ./install.sh  # или в указанную папку
SKIP_SETUP=1 ./install.sh          # только обновить модуль в готовой установке
```

Нужны Python 3.10+, Node.js 22.23.4+, FFmpeg, git, make.

## Ключ

1. На [ask.chadgpt.ru](https://ask.chadgpt.ru) → профиль → **Chad API**:
   пополнить API-баланс и создать ключ.
2. Передать ключ как переменную окружения `CHADGPT_API_KEY`
   - в облачной сессии Claude Code: меню окружения → **Edit** → переменные
     окружения (подхватывается новой сессией);
   - локально: строка `CHADGPT_API_KEY=...` в `OpenMontage/.env`.

Ключ уходит в теле JSON-запроса (так устроен chad Image API), поэтому
подстановка ключа в заголовки через прокси здесь не работает.

Сеть: нужен доступ к `ask.chadgpt.ru` и к CDN, с которого chad отдаёт
готовые файлы (адрес CDN станет известен после первой генерации).

## Что добавляется

| Инструмент | Возможность | Статус |
|---|---|---|
| `chadgpt_image` | `image_generation` | работает по документации chad Image API V2 |
| `chadgpt_video` | `video_generation` | экспериментальный, по умолчанию выключен |

OpenMontage находит инструменты сам; `image_selector` / `video_selector`
могут выбрать их автоматически, или можно попросить агента явно:
«используй chadgpt_image с моделью gpt-img-2».

### chadgpt_image

Модели (поле `model`, по умолчанию `flux-2-pro`): `flux-2-pro`, `flux-2-flex`,
`gpt-img-2`, `gpt-img-1.5`, `gpt-img-low|medium|high`, `gemini-3-pro-image`,
`gemini-3.1-flash-image-preview`, `gemini-2.5-flash-image`, `seedream-4.5`,
`seedream-4`, `seedream-5-lite`, `flux-1.1-pro-ultra`, `flux-1.1-pro`,
`flux-1-schnell`, `flux-kontext-pro`, `flux-kontext-max`, `imagen-4`,
`imagen-4-fast`, `imagen-4-ultra`, `mj-7`, `mj-6.1`, `mj-6`, `mj-5.2`,
`recraft-v4-pro-svg`, `recraft-v4-svg`, `recraft-v3-svg`, `stdiff-m`.

- `aspect_ratio` приводится к ближайшему, который поддерживает модель
  (или выводится из `width`/`height`); точных размеров в пикселях chad не даёт.
- Референсы: `image_url(s)` и локальные `image_path(s)` (для моделей,
  принимающих base64). Kontext — ровно одна ссылка.
- `resolution` → `size_preset` (Gemini 3, Seedream), `n` = 1|4 для
  `flux-1-schnell` и `stdiff-m`, `seed` где поддерживается.
- `extra_params` — любые поля модели как есть (например `stylize` для
  Midjourney, `style` для `recraft-v3-svg`).
- Midjourney возвращает 4 картинки: `out.png`, `out_2.png`, …; Recraft — SVG.
- Цена в рублях, поэтому `estimate_cost` просит уточнить цену
  (`PriceQuoteRequired`), а не выдумывает долларовую сумму.

### chadgpt_video (экспериментально)

chad не документирует видео в API. Без ключа маршрут
`/api/v2/public/{model}/imagine` отвечает «invalid-api-key» (а не 404) для
`seedance-2.0`, `kling-2.6-pro`, `grok-imagine-video`, — значит, модели там
есть, но их параметры неизвестны. Инструмент шлёт `prompt` и
`aspect_ratio`, а `duration` и `image_urls` — только если заданы
(названия полей — предположение); остальное — через `extra_params`.

Инструмент считается недоступным, пока не задано `CHADGPT_VIDEO_ENABLED=1`,
чтобы автоматический выбор не тратил деньги вслепую. Сначала сделайте один
короткий тестовый ролик вручную.

## Тесты

```bash
cd ../OpenMontage && .venv/bin/python -m pytest tests/tools/test_chadgpt.py -q
```

Тесты не ходят в сеть и не требуют ключа.

## Лицензия

AGPL-3.0-or-later, как и OpenMontage (модуль импортирует его код). См. `LICENSE`.
