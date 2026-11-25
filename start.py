# Диагностический запуск: показывает в логе, почему падает main.py
import os, sys, time, runpy, traceback

def log(*a):
    print("DIAG:", *a, flush=True)

here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)
sys.path.insert(0, here)
log("python", sys.version.split()[0])
log("files", sorted(os.listdir(here)))
try:
    data = open("main.py", "rb").read()
    log("main.py bytes =", len(data), "lines =", data.count(b"\n"))
    runpy.run_path("main.py", run_name="__main__")
    log("main.py завершился без ошибки (сервер не остался работать)")
except BaseException:
    log("ОШИБКА:")
    traceback.print_exc(file=sys.stdout)
sys.stdout.flush()
time.sleep(20)  # чтобы Render успел записать лог
sys.exit(1)
