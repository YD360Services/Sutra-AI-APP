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
        private const int WH_MOUSE_LL = 14;

        private const int WM_KEYDOWN = 0x0100;
        private const int WM_KEYUP = 0x0101;
        private const int WM_SYSKEYDOWN = 0x0104;
        private const int WM_SYSKEYUP = 0x0105;

        private const int WM_LBUTTONDOWN = 0x0201;
        private const int WM_RBUTTONDOWN = 0x0204;
        private const int WM_MBUTTONDOWN = 0x0207;

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
        private delegate IntPtr LowLevelMouseProc(int nCode, IntPtr wParam, IntPtr lParam);

        private static LowLevelKeyboardProc _keyProc = KeyboardHookCallback;
        private static LowLevelMouseProc _mouseProc = MouseHookCallback;

        private static IntPtr _keyHookID = IntPtr.Zero;
        private static IntPtr _mouseHookID = IntPtr.Zero;

        private static volatile bool _stealthTypingActive = false;
        private static volatile int _inputMinX = 0;
        private static volatile int _inputMinY = 0;
        private static volatile int _inputMaxX = 0;
        private static volatile int _inputMaxY = 0;

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelKeyboardProc lpfn, IntPtr hMod, uint dwThreadId);

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelMouseProc lpfn, IntPtr hMod, uint dwThreadId);

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
                        if (line.StartsWith("START_TYPING", StringComparison.OrdinalIgnoreCase))
                        {
                            // Optional coords: START_TYPING minX minY maxX maxY
                            string[] parts = line.Split(new char[] { ' ', '\t', ',' }, StringSplitOptions.RemoveEmptyEntries);
                            if (parts.Length >= 5)
                            {
                                int minX = 0, minY = 0, maxX = 0, maxY = 0;
                                int.TryParse(parts[1], out minX);
                                int.TryParse(parts[2], out minY);
                                int.TryParse(parts[3], out maxX);
                                int.TryParse(parts[4], out maxY);
                                _inputMinX = minX;
                                _inputMinY = minY;
                                _inputMaxX = maxX;
                                _inputMaxY = maxY;
                            }
                            else
                            {
                                _inputMinX = _inputMinY = _inputMaxX = _inputMaxY = 0;
                            }
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
                            UnhookAll();
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
                    UnhookAll();
                    Environment.Exit(0);
                }
            });
            stdinThread.IsBackground = true;
            stdinThread.Start();

            // Set low-level keyboard and mouse hooks
            using (Process curProcess = Process.GetCurrentProcess())
            using (ProcessModule curModule = curProcess.MainModule)
            {
                IntPtr modHandle = GetModuleHandle(curModule.ModuleName);
                _keyHookID = SetWindowsHookEx(WH_KEYBOARD_LL, _keyProc, modHandle, 0);
                _mouseHookID = SetWindowsHookEx(WH_MOUSE_LL, _mouseProc, modHandle, 0);
            }

            // Standard Windows Message Loop
            MSG msg;
            while (GetMessage(out msg, IntPtr.Zero, 0, 0) > 0)
            {
                TranslateMessage(ref msg);
                DispatchMessage(ref msg);
            }

            UnhookAll();
        }

        private static void UnhookAll()
        {
            if (_keyHookID != IntPtr.Zero)
            {
                UnhookWindowsHookEx(_keyHookID);
                _keyHookID = IntPtr.Zero;
            }
            if (_mouseHookID != IntPtr.Zero)
            {
                UnhookWindowsHookEx(_mouseHookID);
                _mouseHookID = IntPtr.Zero;
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

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int x;
            public int y;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct MSLLHOOKSTRUCT
        {
            public POINT pt;
            public uint mouseData;
            public uint flags;
            public uint time;
            public IntPtr dwExtraInfo;
        }

        private static IntPtr MouseHookCallback(int nCode, IntPtr wParam, IntPtr lParam)
        {
            if (nCode >= 0 && _stealthTypingActive)
            {
                int msg = wParam.ToInt32();
                if (msg == WM_LBUTTONDOWN || msg == WM_RBUTTONDOWN || msg == WM_MBUTTONDOWN)
                {
                    MSLLHOOKSTRUCT mouseStruct = (MSLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(MSLLHOOKSTRUCT));
                    int mx = mouseStruct.pt.x;
                    int my = mouseStruct.pt.y;

                    bool hasValidRect = (_inputMaxX > _inputMinX && _inputMaxY > _inputMinY);
                    bool isInsideInput = hasValidRect && (mx >= _inputMinX && mx <= _inputMaxX && my >= _inputMinY && my <= _inputMaxY);

                    // If clicked outside the input element, immediately end stealth typing!
                    if (!isInsideInput)
                    {
                        _stealthTypingActive = false;
                        Console.WriteLine("KEY:{\"action\":\"escape\"}");
                        Console.Out.Flush();
                    }
                }
            }

            return CallNextHookEx(_mouseHookID, nCode, wParam, lParam);
        }

        private static IntPtr KeyboardHookCallback(int nCode, IntPtr wParam, IntPtr lParam)
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
                        return CallNextHookEx(_keyHookID, nCode, wParam, lParam);
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

                    if (isShift) keyState[VK_SHIFT] = 0x80;
                    else keyState[VK_SHIFT] = 0x00;

                    short caps = GetAsyncKeyState(VK_CAPITAL);
                    if ((caps & 0x0001) != 0 || (caps & 0x8000) != 0) keyState[VK_CAPITAL] = 0x01;

                    StringBuilder sb = new StringBuilder(16);
                    uint scanCode = MapVirtualKey(vkCode, 0);
                    int res = ToUnicode(vkCode, scanCode, keyState, sb, sb.Capacity, 0);

                    if (res > 0)
                    {
                        string text = sb.ToString();
                        string escaped = text.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "").Replace("\n", "");
                        Console.WriteLine("KEY:{\"action\":\"char\",\"char\":\"" + escaped + "\"}");
                        Console.Out.Flush();
                        return (IntPtr)1; // SWALLOW keystroke
                    }
                    else if (vkCode == VK_SPACE)
                    {
                        Console.WriteLine("KEY:{\"action\":\"char\",\"char\":\" \"}");
                        Console.Out.Flush();
                        return (IntPtr)1;
                    }
                }
            }

            return CallNextHookEx(_keyHookID, nCode, wParam, lParam);
        }
    }
}
