#!/bin/bash
# Export YouTube cookies for yt-dlp

echo "=== YouTube Cookie Exporter ==="
echo ""
echo "Выберите метод:"
echo "1) Экспорт из Firefox (требует установленного браузера)"
echo "2) Экспорт из Chrome (требует установленного браузера)"
echo "3) Ручной ввод (для использования на Render через переменные окружения)"
echo ""
read -p "Выберите вариант [1-3]: " choice

case $choice in
    1)
        if command -v yt-dlp &> /dev/null; then
            yt-dlp --cookies-from-browser firefox --cookies cookies.txt "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --skip-download
            echo "✓ Cookies экспортированы в cookies.txt"
            echo "Скопируйте этот файл в ~/.config/yt-dlp/cookies.txt"
        else
            echo "✗ yt-dlp не установлен"
            exit 1
        fi
        ;;
    2)
        if command -v yt-dlp &> /dev/null; then
            yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --skip-download
            echo "✓ Cookies экспортированы в cookies.txt"
            echo "Скопируйте этот файл в ~/.config/yt-dlp/cookies.txt"
        else
            echo "✗ yt-dlp не установлен"
            exit 1
        fi
        ;;
    3)
        echo ""
        echo "=== Инструкция для Render ==="
        echo ""
        echo "1. Установите расширение 'Get cookies.txt LOCALLY' в браузере"
        echo "2. Откройте YouTube и авторизуйтесь"
        echo "3. Нажмите на расширение и скачайте cookies.txt"
        echo "4. Конвертируйте в base64:"
        echo "   base64 -w 0 cookies.txt > cookies_base64.txt"
        echo ""
        echo "5. На Render добавьте переменную окружения:"
        echo "   YT_COOKIES_BASE64=<содержимое cookies_base64.txt>"
        echo ""
        echo "6. Бот автоматически создаст cookies файл при запуске"
        ;;
    *)
        echo "Неверный выбор"
        exit 1
        ;;
esac
