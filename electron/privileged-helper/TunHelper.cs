using System;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

namespace AkagiNg.Privileged
{
    internal static class TunHelper
    {
        private const int ConnectTimeoutMs = 30000;
        private const int ShutdownTimeoutMs = 3000;
        private const int MaxCommandLength = 16384;
        private const string ExpectedMihomoSha256 = "82cd796a23492f43a71c1ec27e4e5e0b3d58932014da5a36e79ed9b11fee8162";
        private static readonly object WriteLock = new object();
        private static StreamWriter writer;
        private static Process mihomoProcess;
        private static bool stopping;
        private static IntPtr jobHandle = IntPtr.Zero;
        private static string protectedWorkDir;

        [STAThread]
        private static int Main(string[] args)
        {
#if AKAGI_HELPER_TEST
            if (args.Length == 2 && args[0] == "--validate-config")
            {
                try
                {
                    ValidateConfigContent(ReadLockedFile(args[1], 256 * 1024));
                    return 0;
                }
                catch { return 2; }
            }
#endif
            try
            {
                string pipeName = ParsePipeName(args);
                string binaryPath = ResolveBundledMihomo();

                using (NamedPipeClientStream pipe = new NamedPipeClientStream(
                    ".",
                    pipeName,
                    PipeDirection.InOut,
                    PipeOptions.Asynchronous))
                {
                    pipe.Connect(ConnectTimeoutMs);
                    using (StreamReader reader = new StreamReader(pipe, new UTF8Encoding(false), false, 4096, true))
                    using (StreamWriter localWriter = new StreamWriter(pipe, new UTF8Encoding(false), 4096, true))
                    {
                        localWriter.AutoFlush = true;
                        writer = localWriter;
                        Send("HELLO\t1");

                        string startCommand = ReadBoundedLine(reader);
                        string[] startParts = startCommand.Split('\t');
                        if (startParts.Length != 3 || startParts[0] != "START")
                        {
                            throw new InvalidDataException("Expected a single START command.");
                        }

                        string workDir = DecodePath(startParts[1]);
                        string configPath = DecodePath(startParts[2]);
                        ValidateRuntimePaths(workDir, configPath);
                        RuntimePaths runtime = PrepareProtectedRuntime(binaryPath, configPath);
                        protectedWorkDir = runtime.WorkDir;
                        StartMihomo(runtime.BinaryPath, runtime.WorkDir, runtime.ConfigPath);

                        while (true)
                        {
                            string command = ReadBoundedLine(reader);
                            if (command == "STOP")
                            {
                                stopping = true;
                                StopMihomo();
                                Send("STOPPED");
                                return 0;
                            }

                            Send("ERROR\t" + EncodeText("Unsupported helper command."));
                        }
                    }
                }
            }
            catch (EndOfStreamException)
            {
                return 0;
            }
            catch (Exception error)
            {
                Send("ERROR\t" + EncodeText(SafeMessage(error)));
                return 1;
            }
            finally
            {
                stopping = true;
                StopMihomo();
                CloseJob();
                CleanupProtectedRuntime();
                writer = null;
            }
        }

        private static string ParsePipeName(string[] args)
        {
            if (args.Length != 2 || args[0] != "--pipe")
            {
                throw new ArgumentException("Usage: AkagiNg.TunHelper.exe --pipe <pipe-name>");
            }

            string value = args[1] ?? string.Empty;
            if (!Regex.IsMatch(value, "^akagi-ng-tun-[a-f0-9]{64}$", RegexOptions.CultureInvariant))
            {
                throw new ArgumentException("Invalid pipe name.");
            }
            return value;
        }

        private static string ResolveBundledMihomo()
        {
            string helperDir = AppDomain.CurrentDomain.BaseDirectory;
            string path = Path.GetFullPath(Path.Combine(
                helperDir,
                "..",
                "mihomo",
                "windows-x64",
                "mihomo.exe"));
            if (!File.Exists(path))
            {
                throw new FileNotFoundException("Bundled mihomo executable was not found.");
            }
            RejectReparsePoint(path, "mihomo executable");
            return path;
        }

        private static void ValidateRuntimePaths(string workDir, string configPath)
        {
            string normalizedWorkDir = Path.GetFullPath(workDir).TrimEnd(Path.DirectorySeparatorChar);
            string normalizedConfigPath = Path.GetFullPath(configPath);
            if (!string.Equals(
                Path.GetDirectoryName(normalizedConfigPath),
                normalizedWorkDir,
                StringComparison.OrdinalIgnoreCase))
            {
                throw new UnauthorizedAccessException("The TUN config must be inside its work directory.");
            }
            if (!string.Equals(Path.GetFileName(normalizedConfigPath), "config.yaml", StringComparison.OrdinalIgnoreCase))
            {
                throw new UnauthorizedAccessException("Unexpected TUN config filename.");
            }
            if (!Directory.Exists(normalizedWorkDir) || !File.Exists(normalizedConfigPath))
            {
                throw new FileNotFoundException("The TUN work directory or config file does not exist.");
            }

            RejectReparsePoint(normalizedWorkDir, "work directory");
            RejectReparsePoint(normalizedConfigPath, "config file");
            FileInfo config = new FileInfo(normalizedConfigPath);
            if (config.Length <= 0 || config.Length > 256 * 1024)
            {
                throw new InvalidDataException("The TUN config size is invalid.");
            }
        }

        private static RuntimePaths PrepareProtectedRuntime(string sourceBinary, string sourceConfig)
        {
            string programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            string protectedRoot = Path.Combine(programData, "Akagi-NG", "tun-v1");
            CreateProtectedDirectory(protectedRoot);

            string protectedBinary = Path.Combine(protectedRoot, "mihomo.exe");
            InstallVerifiedBinary(sourceBinary, protectedBinary);

            string sessionDir = Path.Combine(protectedRoot, "sessions", Guid.NewGuid().ToString("N"));
            CreateProtectedDirectory(sessionDir);
            string protectedConfig = Path.Combine(sessionDir, "config.yaml");
            byte[] configBytes = ReadLockedFile(sourceConfig, 256 * 1024);
            ValidateConfigContent(configBytes);
            File.WriteAllBytes(protectedConfig, configBytes);
            ApplyProtectedFileAcl(protectedConfig);

            return new RuntimePaths(protectedBinary, sessionDir, protectedConfig);
        }

        private static void InstallVerifiedBinary(string sourcePath, string destinationPath)
        {
            string temporaryPath = destinationPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                using (FileStream source = new FileStream(
                    sourcePath,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.Read,
                    1024 * 1024,
                    FileOptions.SequentialScan))
                using (FileStream destination = new FileStream(
                    temporaryPath,
                    FileMode.CreateNew,
                    FileAccess.Write,
                    FileShare.None,
                    1024 * 1024,
                    FileOptions.WriteThrough))
                using (SHA256 sha256 = SHA256.Create())
                using (CryptoStream hashingStream = new CryptoStream(Stream.Null, sha256, CryptoStreamMode.Write))
                {
                    byte[] buffer = new byte[1024 * 1024];
                    int count;
                    while ((count = source.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        destination.Write(buffer, 0, count);
                        hashingStream.Write(buffer, 0, count);
                    }
                    hashingStream.FlushFinalBlock();
                    destination.Flush(true);
                    string actualHash = ToHex(sha256.Hash);
                    if (!FixedTimeEquals(actualHash, ExpectedMihomoSha256))
                    {
                        throw new UnauthorizedAccessException("Bundled mihomo failed the pinned SHA-256 check.");
                    }
                }

                ApplyProtectedFileAcl(temporaryPath);
                if (File.Exists(destinationPath)) File.Delete(destinationPath);
                File.Move(temporaryPath, destinationPath);
            }
            finally
            {
                try { if (File.Exists(temporaryPath)) File.Delete(temporaryPath); } catch { }
            }
        }

        private static byte[] ReadLockedFile(string path, int maximumBytes)
        {
            using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (stream.Length <= 0 || stream.Length > maximumBytes)
                {
                    throw new InvalidDataException("The TUN config size is invalid.");
                }
                byte[] data = new byte[stream.Length];
                int offset = 0;
                while (offset < data.Length)
                {
                    int count = stream.Read(data, offset, data.Length - offset);
                    if (count == 0) throw new EndOfStreamException("The TUN config changed while reading.");
                    offset += count;
                }
                return data;
            }
        }

        private static void ValidateConfigContent(byte[] bytes)
        {
            string json = new UTF8Encoding(false, true).GetString(bytes);
            JavaScriptSerializer serializer = new JavaScriptSerializer { MaxJsonLength = 256 * 1024 };
            Dictionary<string, object> root = serializer.DeserializeObject(json) as Dictionary<string, object>;
            if (root == null) throw new InvalidDataException("The TUN config root must be an object.");

            RequireExactKeys(root, new[] {
                "mode", "log-level", "ipv6", "allow-lan", "mixed-port", "external-controller",
                "secret", "find-process-mode", "tun", "sniffer", "proxies", "rules"
            }, "config");
            RequireString(root, "mode", "rule");
            RequireString(root, "log-level", "info");
            RequireBoolean(root, "ipv6", false);
            RequireBoolean(root, "allow-lan", false);
            RequireString(root, "find-process-mode", "strict");
            int mixedPort = RequirePort(root, "mixed-port");
            int controllerPort = ParseLoopbackController(root["external-controller"] as string);
            if (mixedPort == controllerPort) throw new InvalidDataException("TUN ports must be unique.");
            string secret = root["secret"] as string;
            if (secret == null || !Regex.IsMatch(secret, "^[a-f0-9]{48}$", RegexOptions.CultureInvariant))
                throw new InvalidDataException("Invalid TUN controller secret.");

            Dictionary<string, object> tun = root["tun"] as Dictionary<string, object>;
            if (tun == null) throw new InvalidDataException("Invalid TUN settings.");
            RequireExactKeys(tun, new[] { "enable", "stack", "auto-route", "auto-detect-interface", "strict-route" }, "tun");
            RequireBoolean(tun, "enable", true);
            RequireString(tun, "stack", "mixed");
            RequireBoolean(tun, "auto-route", true);
            RequireBoolean(tun, "auto-detect-interface", true);
            if (!(tun["strict-route"] is bool)) throw new InvalidDataException("Invalid strict-route value.");

            object[] proxies = root["proxies"] as object[];
            if (proxies == null || proxies.Length != 1) throw new InvalidDataException("Exactly one MITM proxy is required.");
            Dictionary<string, object> proxy = proxies[0] as Dictionary<string, object>;
            if (proxy == null) throw new InvalidDataException("Invalid MITM proxy.");
            RequireExactKeys(proxy, new[] { "name", "type", "server", "port" }, "proxy");
            RequireString(proxy, "name", "Akagi-Mitm");
            RequireString(proxy, "type", "http");
            RequirePort(proxy, "port");
            string server = proxy["server"] as string;
            if (string.IsNullOrWhiteSpace(server) || server.Length > 253 || Regex.IsMatch(server, "[\\x00-\\x20]"))
                throw new InvalidDataException("Invalid MITM proxy host.");

            object[] rules = root["rules"] as object[];
            if (rules == null || rules.Length == 0 || rules.Length > 64)
                throw new InvalidDataException("Invalid TUN rule count.");
            foreach (object ruleValue in rules)
            {
                string rule = ruleValue as string;
                if (string.IsNullOrWhiteSpace(rule) || rule.Length > 512 || Regex.IsMatch(rule, "[\\r\\n\\x00]"))
                    throw new InvalidDataException("Invalid TUN rule.");
            }
            if (!string.Equals(rules[rules.Length - 1] as string, "MATCH,DIRECT", StringComparison.Ordinal))
                throw new InvalidDataException("The final TUN rule must be MATCH,DIRECT.");
        }

        private static void RequireExactKeys(Dictionary<string, object> value, string[] keys, string label)
        {
            HashSet<string> expected = new HashSet<string>(keys, StringComparer.Ordinal);
            if (value.Count != expected.Count) throw new InvalidDataException("Unexpected " + label + " fields.");
            foreach (string key in value.Keys)
                if (!expected.Contains(key)) throw new InvalidDataException("Unexpected " + label + " field.");
        }

        private static void RequireString(Dictionary<string, object> value, string key, string expected)
        {
            if (!value.ContainsKey(key) || !string.Equals(value[key] as string, expected, StringComparison.Ordinal))
                throw new InvalidDataException("Invalid " + key + " value.");
        }

        private static void RequireBoolean(Dictionary<string, object> value, string key, bool expected)
        {
            if (!value.ContainsKey(key) || !(value[key] is bool) || (bool)value[key] != expected)
                throw new InvalidDataException("Invalid " + key + " value.");
        }

        private static int RequirePort(Dictionary<string, object> value, string key)
        {
            if (!value.ContainsKey(key) || !(value[key] is int)) throw new InvalidDataException("Invalid " + key + ".");
            int port = (int)value[key];
            if (port < 1 || port > 65535) throw new InvalidDataException("Invalid " + key + ".");
            return port;
        }

        private static int ParseLoopbackController(string value)
        {
            if (string.IsNullOrEmpty(value) || !value.StartsWith("127.0.0.1:", StringComparison.Ordinal))
                throw new InvalidDataException("The TUN controller must bind to loopback.");
            int port;
            if (!int.TryParse(value.Substring("127.0.0.1:".Length), out port) || port < 1 || port > 65535)
                throw new InvalidDataException("Invalid TUN controller port.");
            return port;
        }

        private static void CreateProtectedDirectory(string path)
        {
            Directory.CreateDirectory(path);
            DirectorySecurity security = new DirectorySecurity();
            security.SetAccessRuleProtection(true, false);
            InheritanceFlags inheritance = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
            security.AddAccessRule(new FileSystemAccessRule(
                new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                FileSystemRights.FullControl, inheritance, PropagationFlags.None, AccessControlType.Allow));
            security.AddAccessRule(new FileSystemAccessRule(
                new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null),
                FileSystemRights.FullControl, inheritance, PropagationFlags.None, AccessControlType.Allow));
            Directory.SetAccessControl(path, security);
        }

        private static void ApplyProtectedFileAcl(string path)
        {
            FileSecurity security = new FileSecurity();
            security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new FileSystemAccessRule(
                new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                FileSystemRights.FullControl, AccessControlType.Allow));
            security.AddAccessRule(new FileSystemAccessRule(
                new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null),
                FileSystemRights.FullControl, AccessControlType.Allow));
            File.SetAccessControl(path, security);
        }

        private static string ToHex(byte[] bytes)
        {
            StringBuilder result = new StringBuilder(bytes.Length * 2);
            foreach (byte value in bytes) result.Append(value.ToString("x2"));
            return result.ToString();
        }

        private static bool FixedTimeEquals(string left, string right)
        {
            if (left == null || right == null || left.Length != right.Length) return false;
            int difference = 0;
            for (int index = 0; index < left.Length; index++) difference |= left[index] ^ right[index];
            return difference == 0;
        }

        private static void RejectReparsePoint(string path, string label)
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            {
                throw new UnauthorizedAccessException("The " + label + " cannot be a reparse point.");
            }
        }

        private static void StartMihomo(string binaryPath, string workDir, string configPath)
        {
            ProcessStartInfo startInfo = new ProcessStartInfo
            {
                FileName = binaryPath,
                Arguments = "-d " + Quote(workDir) + " -f " + Quote(configPath),
                WorkingDirectory = workDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };

            Process process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
            {
                if (!string.IsNullOrEmpty(eventArgs.Data))
                {
                    Send("LOG\tstdout\t" + EncodeText(eventArgs.Data));
                }
            };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
            {
                if (!string.IsNullOrEmpty(eventArgs.Data))
                {
                    Send("LOG\tstderr\t" + EncodeText(eventArgs.Data));
                }
            };
            process.Exited += delegate
            {
                int code = GetExitCode(process);
                Send("EXITED\t" + code.ToString(System.Globalization.CultureInfo.InvariantCulture));
                if (!stopping)
                {
                    try { writer.BaseStream.Close(); } catch { }
                }
            };

            if (!process.Start())
            {
                throw new InvalidOperationException("Failed to start bundled mihomo.");
            }
            mihomoProcess = process;
            AssignToKillOnCloseJob(process);
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            Send("STARTED\t" + process.Id.ToString(System.Globalization.CultureInfo.InvariantCulture));
        }

        private static void StopMihomo()
        {
            Process process = Interlocked.Exchange(ref mihomoProcess, null);
            if (process == null)
            {
                return;
            }

            try
            {
                if (!process.HasExited)
                {
                    process.Kill();
                    process.WaitForExit(ShutdownTimeoutMs);
                }
            }
            catch { }
            finally
            {
                process.Dispose();
            }
        }

        private static void AssignToKillOnCloseJob(Process process)
        {
            jobHandle = CreateJobObject(IntPtr.Zero, null);
            if (jobHandle == IntPtr.Zero) throw new InvalidOperationException("Failed to create the TUN process job.");

            JOBOBJECT_EXTENDED_LIMIT_INFORMATION information = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            information.BasicLimitInformation.LimitFlags = 0x00002000;
            int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            IntPtr pointer = Marshal.AllocHGlobal(length);
            try
            {
                Marshal.StructureToPtr(information, pointer, false);
                if (!SetInformationJobObject(jobHandle, 9, pointer, (uint)length) ||
                    !AssignProcessToJobObject(jobHandle, process.Handle))
                    throw new InvalidOperationException("Failed to contain the TUN process.");
            }
            finally { Marshal.FreeHGlobal(pointer); }
        }

        private static void CloseJob()
        {
            IntPtr handle = Interlocked.Exchange(ref jobHandle, IntPtr.Zero);
            if (handle != IntPtr.Zero) CloseHandle(handle);
        }

        private static void CleanupProtectedRuntime()
        {
            string directory = protectedWorkDir;
            protectedWorkDir = null;
            if (string.IsNullOrEmpty(directory)) return;
            try { Directory.Delete(directory, true); } catch { }
        }

        private static int GetExitCode(Process process)
        {
            try { return process.ExitCode; }
            catch { return -1; }
        }

        private static string ReadBoundedLine(StreamReader reader)
        {
            string value = reader.ReadLine();
            if (value == null)
            {
                throw new EndOfStreamException("The desktop control pipe was closed.");
            }
            if (value.Length == 0 || value.Length > MaxCommandLength)
            {
                throw new InvalidDataException("Invalid helper command length.");
            }
            return value;
        }

        private static string DecodePath(string value)
        {
            byte[] bytes = Convert.FromBase64String(value);
            if (bytes.Length == 0 || bytes.Length > 8192)
            {
                throw new InvalidDataException("Invalid encoded path length.");
            }
            return new UTF8Encoding(false, true).GetString(bytes);
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static string EncodeText(string value)
        {
            string bounded = value ?? string.Empty;
            if (bounded.Length > 8192)
            {
                bounded = bounded.Substring(0, 8192);
            }
            return Convert.ToBase64String(Encoding.UTF8.GetBytes(bounded));
        }

        private static string SafeMessage(Exception error)
        {
            if (error is TimeoutException) return "Timed out waiting for the desktop control pipe.";
            if (error is UnauthorizedAccessException) return error.Message;
            if (error is FileNotFoundException) return error.Message;
            if (error is InvalidDataException || error is ArgumentException) return error.Message;
            return "The privileged TUN helper failed unexpectedly.";
        }

        private static void Send(string message)
        {
            lock (WriteLock)
            {
                try
                {
                    if (writer != null)
                    {
                        writer.WriteLine(message);
                    }
                }
                catch { }
            }
        }

        private sealed class RuntimePaths
        {
            internal readonly string BinaryPath;
            internal readonly string WorkDir;
            internal readonly string ConfigPath;

            internal RuntimePaths(string binaryPath, string workDir, string configPath)
            {
                BinaryPath = binaryPath;
                WorkDir = workDir;
                ConfigPath = configPath;
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS
        {
            internal ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            internal ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            internal long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            internal uint LimitFlags;
            internal UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            internal uint ActiveProcessLimit;
            internal long Affinity;
            internal uint PriorityClass, SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            internal JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            internal IO_COUNTERS IoInfo;
            internal UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr CreateJobObject(IntPtr securityAttributes, string name);

        [DllImport("kernel32.dll")]
        private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

        [DllImport("kernel32.dll")]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll")]
        private static extern bool CloseHandle(IntPtr handle);
    }
}
