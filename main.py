import os
import subprocess
import sys

print("🚀 Main launcher starting...")

# Проверяем где мы
print(f"📁 Current dir: {os.getcwd()}")
print(f"📄 Files: {os.listdir('.')}")

# Ищем bot.py
if os.path.exists("bot.py"):
    print("✅ Found bot.py in current directory")
    script = "bot.py"
elif os.path.exists("/app/bot.py"):
    print("✅ Found bot.py in /app")
    os.chdir("/app")
    script = "bot.py"
else:
    # Ищем в других местах
    for root, dirs, files in os.walk("/"):
        if "bot.py" in files:
            print(f"✅ Found bot.py in {root}")
            os.chdir(root)
            script = "bot.py"
            break
    else:
        print("❌ ERROR: bot.py not found anywhere!")
        sys.exit(1)

# Запускаем бота
print(f"✅ Starting {script}...")
subprocess.run([sys.executable, script])
