# TGU RU Checker — тестовая интеграция Yandex Cloud

Ветка прототипа. **Функция не развернута**, пока владелец не настроит Yandex Cloud. Обновление основного каталога продолжит работать без неё.

## Текущий прогресс

- Yandex Cloud Function **развёрнута владельцем**, ручное тестирование успешно: MTProto-прокси `www.yaho.com.maroochia.co.uk:443` прошёл из Yandex Cloud с RTT 203 мс (18 ноября 2026, UTC). Этот ручной результат пока не записан автоматически в `ru-health.json`.
- GitHub Actions **ещё не авторизован** для вызова этой функции. Секреты и включение основного расписания остаются отдельным шагом.
- До публикации ветки в `main` автоматические проверки и новый интерфейс сайта не работают в production.

## Как это работает

1. Существующий GitHub Actions собирает глобально проверенные MTProto/SOCKS5 прокси.
2. При наличии Yandex Secrets отправляет в приватную Cloud Function пакет максимум из 32 адресов. До 8 адресов из каждой партии (25%) выбираются из **ранее подтверждённых RU-прокси**, которые не перепроверялись 45 минут или дольше; оставшиеся адреса проверяются по кругу с сохранением курсора. Если подходящих RU-прокси нет, проверяется полный пакет новых адресов.
3. Yandex Function проверяет реальные MTProto obfuscated2/req_pq_multi и SOCKS5 CONNECT до Telegram DC с исходящей сетью облака РФ.
4. Результаты возвращаются в GitHub Actions и агрегируются в `data/ru-health.json` и `docs/data/ru-health.json`, где хранятся последние результаты за 24 часа.
5. На сайте `RU Verified` показывает проверки **из сети Yandex Cloud**, но **не гарантирует** работу на Мегафоне, МТС, Билайне или T2.

## Настройка

1. Зарегистрируйте биллинговый аккаунт Yandex Cloud, настройте бюджет и уведомления. Бесплатные квоты не являются жёстким пределом затрат.
2. Создайте **приватную** Cloud Function `tgu-ru-checker`, runtime **Python 3.12**, точка входа `main.handler`, память **256 МБ**, timeout **90 секунд**. Публичный вызов **не включать**.
3. Соберите ZIP на своём компьютере из корня репозитория: `python scripts/package_ru_function.py`. Загрузите `tgu-ru-checker-function.zip` как исходники версии функции. Зависимость `cryptography` указана в корневом `requirements.txt`.
4. Создайте сервисный аккаунт с ролью `functions.functionInvoker` **только для этой функции** и API key с ограничением `yc.serverless.functions.invoke`.
5. В GitHub репозитории `TGU-Proxy-Directory` откройте **Settings → Secrets and variables → Actions** и добавьте секреты:
   - `TGU_RU_FUNCTION_URL`: адрес `https://functions.yandexcloud.net/ID_ФУНКЦИИ`
   - `TGU_RU_FUNCTION_API_KEY`: API-ключ сервисного аккаунта
6. Пока ветка тестовая, в **Actions → Update TGU proxy catalog → Run workflow** выберите ветку `test/ru-checker-yandex-mvp` и установите `ru_smoke=true`. Проверятся только 8 прокси, отчёт появится как артефакт `tgu-ru-cloud-smoke-result`, каталог и сайт не публикуются. Если интерфейс GitHub не показывает параметр ветки, можно запустить из GitHub CLI: `gh workflow run update-proxies.yml --ref test/ru-checker-yandex-mvp -f ru_smoke=true`.
7. После подтверждения результатов и слияния ветки в `main` обычный **Update TGU proxy catalog** будет проверять по 32 прокси за запуск, обновлять `data/ru-health.json` и публиковать данные на GitHub Pages.

GitHub Actions не отправляет Telegram ID, телефон, переписку или результаты пользовательских устройств. Секреты не размещаются в APK, исходном коде и JSON каталога.

## Диагностика исходящего интернета (после установки новой версии функции)

Для теста через раздел **Cloud Functions → Тестирование** используйте входное событие:

```json
{
  "httpMethod": "POST",
  "body": "{\"schema_version\":1,\"mode\":\"network_diagnostics\"}"
}
```

В ответе `network` будет состояние трёх фиксированных тестовых TCP/443 соединений:
`internet_ip` (1.1.1.1), `internet_dns` (example.com) и `telegram_dc` (149.154.167.51).
Эти сетевые адреса нельзя заменить пользовательским параметром. Если первые два не проходят, прежде чем тестировать тысячи прокси, проверьте настройки сети и исходящего NAT для облачной функции.
В обычных результатах прокси добавлены `failure_stage`, `failure_detail`, `elapsed_ms`. Они нужны для диагностики ошибок; глобальный каталог от них не меняется.

## Ограничения

- 32 прокси за запуск, 8 параллельных проверок, до 8 повторных RU-проверок за пакет, не более одного вызова Cloud Function на запуск коллектора.
- Нет отдельного таймера в Яндексе и нет базы данных: состояние только в GitHub JSON.
- Smoke-test запускается вручную, отчёт сохраняется только как Actions Artifact; не создаёт коммита.
- В GitHub Actions шаг проверки помечен `continue-on-error`, чтобы ошибки RU Checker не ломали базовый каталог.
- Данные RU Checker **не изменяют число глобально рабочих прокси** и не используются для выбора прокси в Android до отдельного клиентского обновления.
- Нужна проверка реальных вызовов и стоимости в собственном Yandex Cloud. Без этого статус будет `not_configured`.

Документация: https://yandex.cloud/en/docs/functions/operations/function/auth
Зависимости: https://yandex.cloud/en/docs/functions/lang/python/dependencies
Условия free tier: https://yandex.cloud/en/docs/billing/concepts/serverless-free-tier
