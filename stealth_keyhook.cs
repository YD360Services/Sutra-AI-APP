using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace StealthKeyHook
{
    class Program
    {
        private const int WH_KEYBOARD_LL = 13;
        private const int WM_KEYDOWN = 0x0100;
        private const int WM_KEYUP = 0x0101;
        private const int WM_SYSKEYDOWN = 0x0104;
        private const int WM_SYSKEYUP = 0x0105;

        private const int VK_SHIFT = 0x10;
        private const int VK_CONTROL = 0x11;
        private const int VK_MENU = 0x12; // Alt
        private const int VK_CAPITAL = 0x14; // CapsLock
        private const int VK_BACK = 0x08;
        private const int VK_TAB = 0x09;
        private const int VK_RETURN = 0x0D;
        private const int VK_ESCAPE = 0x1B;
        private const int VK_SPACE = 0x20;
        private const int VK_LEFT = 0x25;
        private const int VK_UP = 0x26;
        private const int VK_RIGHT = 0x27;
        private const int VK_DOWN = 0x28;
        private const int VK_DELETE = 0x2E;
        private const int VK_OEM_5 = 0xDC; // '\' key on standard US keyboard

        private delegate IntPtr LowLevelKeyboardProc(int nCode, IntPtr wParam, IntPtr lParam);
        private static LowLevelKeyboardProc _proc = HookCallback;
        private static IntPtr _hookID = IntPtr.Zero;

        private static volatile bool _stealthTypingActive = false;

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelKeyboardProc lpfn, IntPtr hMod, uint dwThreadId);

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool UnhookWindowsHookEx(IntPtr hhk);

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

        [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr GetModuleHandle(string lpModuleName);

        [DllImport("user32.dll")]
        private static extern short GetAsyncKeyState(int vKey);

        [DllImport("user32.dll")]
        private static extern bool GetKeyboardState(byte[] lpKeyState);

        [DllImport("user32.dll")]
        private static extern int ToUnicode(
            uint wVirtKey,
            uint wScanCode,
            byte[] lpKeyState,
            [Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pwszBuff,
            int cchBuff,
            uint wFlags);

        [DllImport("user32.dll")]
        private static extern uint MapVirtualKey(uint uCode, uint uMapType);

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll", SetLastError = true)]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("user32.dll")]
        private static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();

        static void Main(string[] args)
        {
            Console.OutputEncoding = Encoding.UTF8;
            Console.WriteLine("HOOK_READY");
            Console.Out.Flush();

            // Background thread to listen for stdin commands from Node.js
            Thread stdinThread = new Thread(() =>
            {
                try
                {
                    string line;
                    while ((line = Console.ReadLine()) != null)
                    {
                        line = line.Trim();
                        if (line.Equals("START_TYPING", StringComparison.OrdinalIgnoreCase))
                        {
                            _stealthTypingActive = true;
                            Console.WriteLine("STATUS:TYPING_STARTED");
                            Console.Out.Flush();
                        }
                        else if (line.Equals("STOP_TYPING", StringComparison.OrdinalIgnoreCase))
                        {
                            _stealthTypingActive = false;
                            Console.WriteLine("STATUS:TYPING_STOPPED");
                            Console.Out.Flush();
                        }
                        else if (line.Equals("PING", StringComparison.OrdinalIgnoreCase))
                        {
                            Console.WriteLine("PONG");
                            Console.Out.Flush();
                        }
                        else if (line.Equals("EXIT", StringComparison.OrdinalIgnoreCase))
                        {
                            Unhook();
                            Environment.Exit(0);
                        }
                    }
                }
                catch
                {
                    // Stdin stream closed
                }
                finally
                {
                    Unhook();
                    Environment.Exit(0);
                }
            });
            stdinThread.IsBackground = true;
            stdinThread.Start();

            // Set low-level hook
            using (Process curProcess = Process.GetCurrentProcess())
            using (ProcessModule curModule = curProcess.MainModule)
            {
                _hookID = SetWindowsHookEx(WH_KEYBOARD_LL, _proc, GetModuleHandle(curModule.ModuleName), 0);
            }

            // Standard Windows Message Loop
            MSG msg;
            while (GetMessage(out msg, IntPtr.Zero, 0, 0) > 0)
            {
                TranslateMessage(ref msg);
                DispatchMessage(ref msg);
            }

            Unhook();
        }

        private static void Unhook()
        {
            if (_hookID != IntPtr.Zero)
            {
                UnhookWindowsHookEx(_hookID);
                _hookID = IntPtr.Zero;
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct MSG
        {
            public IntPtr hwnd;
            public uint message;
            public IntPtr wParam;
            public IntPtr lParam;
            public uint time;
            public int pt_x;
            public int pt_y;
        }

        [DllImport("user32.dll")]
        private static extern int GetMessage(out MSG lpMsg, IntPtr hWnd, uint wMsgFilterMin, uint wMsgFilterMax);

        [DllImport("user32.dll")]
        private static extern bool TranslateMessage([In] ref MSG lpMsg);

        [DllImport("user32.dll")]
        private static extern IntPtr DispatchMessage([In] ref MSG lpMsg);

        [StructLayout(LayoutKind.Sequential)]
        private struct KBDLLHOOKSTRUCT
        {
            public uint vkCode;
            public uint scanCode;
            public uint flags;
            public uint time;
            public IntPtr dwExtraInfo;
        }

        private static IntPtr HookCallback(int nCode, IntPtr wParam, IntPtr lParam)
        {
            if (nCode >= 0 && (wParam == (IntPtr)WM_KEYDOWN || wParam == (IntPtr)WM_SYSKEYDOWN))
            {
                KBDLLHOOKSTRUCT kbd = (KBDLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(KBDLLHOOKSTRUCT));
                uint vkCode = kbd.vkCode;

                bool isCtrl = (GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0;
                bool isShift = (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0;
                bool isAlt = (GetAsyncKeyState(VK_MENU) & 0x8000) != 0;

                // Check if user pressed Ctrl+\ or Ctrl+M while typing to toggle/cancel
                if (isCtrl && (vkCode == VK_OEM_5 || vkCode == 'M'))
                {
                    // Let the hotkey trigger through or emit toggle
                    Console.WriteLine("KEY:{\"action\":\"toggle\"}");
                    Console.Out.Flush();
                    return (IntPtr)1; // Swallow shortcut so back window doesn't get it
                }

                if (_stealthTypingActive)
                {
                    // Escape: Cancel typing mode
                    if (vkCode == VK_ESCAPE)
                    {
                        _stealthTypingActive = false;
                        Console.WriteLine("KEY:{\"action\":\"escape\"}");
                        Console.Out.Flush();
                        return (IntPtr)1; // Swallow
                    }

                    // Return / Enter: Submit question
                    if (vkCode == VK_RETURN)
                    {
                        _stealthTypingActive = false;
                        Console.WriteLine("KEY:{\"action\":\"enter\"}");
                        Console.Out.Flush();
                        return (IntPtr)1; // Swallow
                    }

                    // Backspace
                    if (vkCode == VK_BACK)
                    {
                        Console.WriteLine("KEY:{\"action\":\"backspace\",\"ctrl\":" + (isCtrl ? "true" : "false") + "}");
                        Console.Out.Flush();
                        return (IntPtr)1; // Swallow
                    }

                    // Tab: (Insert tab or 2 spaces)
                    if (vkCode == VK_TAB)
                    {
                        Console.WriteLine("KEY:{\"action\":\"char\",\"char\":\"\\t\"}");
                        Console.Out.Flush();
                        return (IntPtr)1; // Swallow
                    }

                    // Ctrl Shortcuts inside typing mode:
                    if (isCtrl)
                    {
                        if (vkCode == 'V' || vkCode == 'v')
                        {
                            Console.WriteLine("KEY:{\"action\":\"paste\"}");
                            Console.Out.Flush();
                            return (IntPtr)1;
                        }
                        if (vkCode == 'A' || vkCode == 'a')
                        {
                            Console.WriteLine("KEY:{\"action\":\"selectAll\"}");
                            Console.Out.Flush();
                            return (IntPtr)1;
                        }
                        if (vkCode == 'C' || vkCode == 'c')
                        {
                            Console.WriteLine("KEY:{\"action\":\"copy\"}");
                            Console.Out.Flush();
                            return (IntPtr)1;
                        }
                        if (vkCode == 'X' || vkCode == 'x')
                        {
                            Console.WriteLine("KEY:{\"action\":\"cut\"}");
                            Console.Out.Flush();
                            return (IntPtr)1;
                        }
                        if (vkCode == 'Z' || vkCode == 'z')
                        {
                            Console.WriteLine("KEY:{\"action\":\"undo\"}");
                            Console.Out.Flush();
                            return (IntPtr)1;
                        }
                        // If it's another Ctrl combo (like Ctrl+Space for capture, Ctrl+Up, etc.), don't swallow it
                        return CallNextHookEx(_hookID, nCode, wParam, lParam);
                    }

                    // Navigation keys
                    if (vkCode == VK_LEFT)
                    {
                        Console.WriteLine("KEY:{\"action\":\"nav\",\"key\":\"ArrowLeft\",\"shift\":" + (isShift ? "true" : "false") + "}");
                        Console.Out.Flush();
                        return (IntPtr)1;
                    }
                    if (vkCode == VK_RIGHT)
                    {
                        Console.WriteLine("KEY:{\"action\":\"nav\",\"key\":\"ArrowRight\",\"shift\":" + (isShift ? "true" : "false") + "}");
                        Console.Out.Flush();
                        return (IntPtr)1;
                    }
                    if (vkCode == VK_DELETE)
                    {
                        Console.WriteLine("KEY:{\"action\":\"delete\"}");
                        Console.Out.Flush();
                        return (IntPtr)1;
                    }

                    // Convert key to Unicode character
                    byte[] keyState = new byte[256];
                    GetKeyboardState(keyState);

                    // Ensure Shift state is accurate
                    if (isShift) keyState[VK_SHIFT] = 0x80;
                    else keyState[VK_SHIFT] = 0x00;

                    // Ensure CapsLock is accounted for
                    short caps = GetAsyncKeyState(VK_CAPITAL);
                    if ((caps & 0x0001) != 0 || (caps & 0x8000) != 0) keyState[VK_CAPITAL] = 0x01;

                    StringBuilder sb = new StringBuilder(16);
                    uint scanCode = MapVirtualKey(vkCode, 0);
                    int res = ToUnicode(vkCode, scanCode, keyState, sb, sb.Capacity, 0);

                    if (res > 0)
                    {
                        string text = sb.ToString();
                        // Escape JSON characters
                        string escaped = text.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "").Replace("\n", "");
                        Console.WriteLine("KEY:{\"action\":\"char\",\"char\":\"" + escaped + "\"}");
                        Console.Out.Flush();
                        return (IntPtr)1; // SWALLOW keystroke so it doesn't leak into background window!
                    }
                    else if (vkCode == VK_SPACE)
                    {
                        Console.WriteLine("KEY:{\"action\":\"char\",\"char\":\" \"}");
                        Console.Out.Flush();
                        return (IntPtr)1;
                    }
                }
            }

            return CallNextHookEx(_hookID, nCode, wParam, lParam);
        }
    }
}
