import tkinter as tk
from aaf2resolve.gui import App
from aaf2resolve.utils import log
from aaf2resolve import APP_NAME

def main():
    log(f"=== {APP_NAME} start ===")
    root = tk.Tk()
    App(root)
    root.mainloop()
    log(f"=== {APP_NAME} end ===")

if __name__ == "__main__":
    main()
