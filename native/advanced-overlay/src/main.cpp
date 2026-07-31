#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <winhttp.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

#include "imgui.h"
#include "imgui_impl_dx11.h"
#include "imgui_impl_win32.h"

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(
    HWND window, UINT message, WPARAM wParam, LPARAM lParam);

namespace {

using Clock = std::chrono::steady_clock;
constexpr wchar_t kWindowClass[] = L"AkagiAdvancedOverlayWindow";
constexpr wchar_t kWindowTitle[] = L"Akagi Advanced Overlay";
constexpr DWORD kExcludeFromCapture = 0x00000011;
constexpr auto kRecommendationTtl = std::chrono::seconds(12);

struct Options {
    std::string sseUrl = "http://127.0.0.1:8765/sse?clientId=advanced-overlay";
    std::string host = "protected";
    bool captureProtection = true;
    bool selfTest = false;
    DWORD parentPid = 0;
};

struct Recommendation {
    std::string action;
    std::string tile;
    std::string consumed;
    float confidence = 0.0F;
};

struct OverlayState {
    std::mutex mutex;
    std::vector<Recommendation> recommendations;
    std::vector<std::string> notifications;
    bool connected = false;
    Clock::time_point updatedAt{};
};

Options ParseOptions(int argc, char** argv) {
    Options result;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument(argv[index]);
        const auto read = [&](std::string_view prefix) -> std::optional<std::string> {
            if (!argument.starts_with(prefix)) return std::nullopt;
            return std::string(argument.substr(prefix.size()));
        };
        if (const auto value = read("--sse=")) result.sseUrl = *value;
        if (const auto value = read("--host=")) result.host = *value;
        if (const auto value = read("--capture-protection=")) {
            result.captureProtection = *value == "true" || *value == "1";
        }
        if (argument == "--self-test") result.selfTest = true;
        if (const auto value = read("--parent-pid=")) {
            try {
                result.parentPid = static_cast<DWORD>(std::stoul(*value));
            } catch (...) {
                result.parentPid = 0;
            }
        }
    }
    return result;
}

std::wstring Utf8ToWide(const std::string& value) {
    if (value.empty()) return {};
    const int size = MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), nullptr, 0);
    if (size <= 0) return {};
    std::wstring result(static_cast<std::size_t>(size), L'\0');
    MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), result.data(), size);
    return result;
}

std::string JoinConsumed(const nlohmann::json& value) {
    if (!value.is_array()) return {};
    std::string result;
    for (const auto& item : value) {
        if (!item.is_string()) continue;
        if (!result.empty()) result += ' ';
        result += item.get<std::string>();
    }
    return result;
}

void ApplyRecommendations(OverlayState& state, const nlohmann::json& data) {
    std::vector<Recommendation> parsed;
    const auto iterator = data.find("recommendations");
    if (iterator != data.end() && iterator->is_array()) {
        for (const auto& item : *iterator) {
            if (!item.is_object()) continue;
            Recommendation recommendation;
            recommendation.action = item.value("action", "");
            recommendation.tile = item.value("tile", "");
            recommendation.confidence = item.value("confidence", 0.0F);
            if (const auto consumed = item.find("consumed"); consumed != item.end()) {
                recommendation.consumed = JoinConsumed(*consumed);
            }
            parsed.push_back(std::move(recommendation));
            if (parsed.size() == 3) break;
        }
    }

    std::scoped_lock lock(state.mutex);
    state.recommendations = std::move(parsed);
    state.updatedAt = Clock::now();
}

void ApplyNotifications(OverlayState& state, const nlohmann::json& data) {
    std::vector<std::string> parsed;
    const auto iterator = data.find("list");
    if (iterator != data.end() && iterator->is_array()) {
        for (const auto& item : *iterator) {
            if (!item.is_object()) continue;
            const std::string text = item.value("msg", item.value("code", ""));
            if (!text.empty()) parsed.push_back(text);
        }
    }
    std::scoped_lock lock(state.mutex);
    state.notifications = std::move(parsed);
}

class SseClient {
public:
    SseClient(std::string url, OverlayState& state) : url_(std::move(url)), state_(state) {}
    ~SseClient() { Stop(); }

    void Start() {
        stop_ = false;
        worker_ = std::thread([this] { Run(); });
    }

    void Stop() {
        stop_ = true;
        if (worker_.joinable()) worker_.join();
    }

private:
    struct ParsedUrl {
        std::wstring host;
        std::wstring path;
        INTERNET_PORT port = 0;
        bool secure = false;
    };

    std::optional<ParsedUrl> ParseUrl() const {
        const std::wstring wide = Utf8ToWide(url_);
        URL_COMPONENTS components{};
        components.dwStructSize = sizeof(components);
        components.dwHostNameLength = static_cast<DWORD>(-1);
        components.dwUrlPathLength = static_cast<DWORD>(-1);
        components.dwExtraInfoLength = static_cast<DWORD>(-1);
        if (!WinHttpCrackUrl(wide.c_str(), 0, 0, &components)) return std::nullopt;

        ParsedUrl result;
        result.host.assign(components.lpszHostName, components.dwHostNameLength);
        result.path.assign(components.lpszUrlPath, components.dwUrlPathLength);
        if (components.dwExtraInfoLength > 0) {
            result.path.append(components.lpszExtraInfo, components.dwExtraInfoLength);
        }
        result.port = components.nPort;
        result.secure = components.nScheme == INTERNET_SCHEME_HTTPS;
        return result;
    }

    void SetConnected(bool connected) {
        std::scoped_lock lock(state_.mutex);
        state_.connected = connected;
    }

    void ProcessEvent(const std::string& block) {
        std::string event;
        std::string data;
        std::size_t cursor = 0;
        while (cursor <= block.size()) {
            const std::size_t end = block.find('\n', cursor);
            std::string_view line(
                block.data() + cursor,
                (end == std::string::npos ? block.size() : end) - cursor);
            if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
            if (line.starts_with("event:")) {
                event = std::string(line.substr(6));
                event.erase(0, event.find_first_not_of(' '));
            } else if (line.starts_with("data:")) {
                std::string_view part = line.substr(5);
                if (!part.empty() && part.front() == ' ') part.remove_prefix(1);
                if (!data.empty()) data += '\n';
                data.append(part);
            }
            if (end == std::string::npos) break;
            cursor = end + 1;
        }
        if (data.empty()) return;
        try {
            const nlohmann::json parsed = nlohmann::json::parse(data);
            if (event == "recommendations") ApplyRecommendations(state_, parsed);
            if (event == "notification") ApplyNotifications(state_, parsed);
        } catch (const std::exception& error) {
            std::cerr << "SSE JSON error: " << error.what() << '\n';
        }
    }

    void Consume(std::string& buffer) {
        for (;;) {
            std::size_t end = buffer.find("\n\n");
            std::size_t delimiter = 2;
            const std::size_t crlf = buffer.find("\r\n\r\n");
            if (crlf != std::string::npos && (end == std::string::npos || crlf < end)) {
                end = crlf;
                delimiter = 4;
            }
            if (end == std::string::npos) return;
            ProcessEvent(buffer.substr(0, end));
            buffer.erase(0, end + delimiter);
        }
    }

    bool ConnectOnce(const ParsedUrl& url) {
        HINTERNET session = WinHttpOpen(
            L"Akagi-NG/1.1.0", WINHTTP_ACCESS_TYPE_NO_PROXY, WINHTTP_NO_PROXY_NAME,
            WINHTTP_NO_PROXY_BYPASS, 0);
        if (!session) return false;
        WinHttpSetTimeouts(session, 2000, 2000, 2000, 2000);

        HINTERNET connection = WinHttpConnect(session, url.host.c_str(), url.port, 0);
        if (!connection) {
            WinHttpCloseHandle(session);
            return false;
        }
        const DWORD flags = url.secure ? WINHTTP_FLAG_SECURE : 0;
        HINTERNET request = WinHttpOpenRequest(
            connection, L"GET", url.path.c_str(), nullptr, WINHTTP_NO_REFERER,
            WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
        if (!request) {
            WinHttpCloseHandle(connection);
            WinHttpCloseHandle(session);
            return false;
        }

        const wchar_t headers[] = L"Accept: text/event-stream\r\nCache-Control: no-cache\r\n";
        const bool sent = WinHttpSendRequest(
            request, headers, static_cast<DWORD>(-1), WINHTTP_NO_REQUEST_DATA, 0, 0, 0) != FALSE;
        const bool received = sent && WinHttpReceiveResponse(request, nullptr) != FALSE;
        if (!received) {
            WinHttpCloseHandle(request);
            WinHttpCloseHandle(connection);
            WinHttpCloseHandle(session);
            return false;
        }

        DWORD status = 0;
        DWORD statusSize = sizeof(status);
        WinHttpQueryHeaders(
            request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &status, &statusSize, WINHTTP_NO_HEADER_INDEX);
        if (status != 200) {
            std::cerr << "SSE HTTP status: " << status << '\n';
            WinHttpCloseHandle(request);
            WinHttpCloseHandle(connection);
            WinHttpCloseHandle(session);
            return false;
        }

        SetConnected(true);
        std::string buffer;
        while (!stop_) {
            DWORD available = 0;
            if (!WinHttpQueryDataAvailable(request, &available) || available == 0) break;
            std::vector<char> chunk(available);
            DWORD read = 0;
            if (!WinHttpReadData(request, chunk.data(), available, &read) || read == 0) break;
            buffer.append(chunk.data(), read);
            Consume(buffer);
        }
        SetConnected(false);
        WinHttpCloseHandle(request);
        WinHttpCloseHandle(connection);
        WinHttpCloseHandle(session);
        return true;
    }

    void Run() {
        const auto parsed = ParseUrl();
        if (!parsed) {
            std::cerr << "Invalid SSE URL.\n";
            return;
        }
        while (!stop_) {
            ConnectOnce(*parsed);
            SetConnected(false);
            for (int step = 0; step < 10 && !stop_; ++step) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        }
    }

    std::string url_;
    OverlayState& state_;
    std::atomic_bool stop_{false};
    std::thread worker_;
};

template <typename T>
void ReleaseCom(T*& object) {
    if (object) {
        object->Release();
        object = nullptr;
    }
}

class Renderer {
public:
    ~Renderer() { Shutdown(); }

    bool Initialize(HWND window, bool protectedWindow) {
        Shutdown();
        window_ = window;
        protectedWindow_ = protectedWindow;
        RECT rectangle{};
        if (!GetClientRect(window_, &rectangle)) return false;
        width_ = static_cast<UINT>(rectangle.right - rectangle.left);
        height_ = static_cast<UINT>(rectangle.bottom - rectangle.top);
        if (width_ == 0 || height_ == 0) return false;

        DXGI_SWAP_CHAIN_DESC description{};
        description.BufferDesc.Width = width_;
        description.BufferDesc.Height = height_;
        description.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        description.SampleDesc.Count = 1;
        description.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
        description.BufferCount = 2;
        description.OutputWindow = window_;
        description.Windowed = TRUE;
        description.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
        constexpr D3D_FEATURE_LEVEL levels[] = {
            D3D_FEATURE_LEVEL_11_0,
            D3D_FEATURE_LEVEL_10_0,
        };
        D3D_FEATURE_LEVEL selected{};
        HRESULT result = CreateDeviceAndSwapChain(
            description, levels, D3D_DRIVER_TYPE_HARDWARE, selected);
        if (FAILED(result)) {
            ReleaseCom(context_);
            ReleaseCom(device_);
            ReleaseCom(swapChain_);
            result = CreateDeviceAndSwapChain(
                description, levels, D3D_DRIVER_TYPE_WARP, selected);
        }
        if (FAILED(result) || !CreateRenderTarget()) {
            Shutdown();
            return false;
        }

        IMGUI_CHECKVERSION();
        ImGui::CreateContext();
        contextCreated_ = true;
        ImGui::StyleColorsDark();
        ImGui::GetIO().IniFilename = nullptr;
        if (!ImGui_ImplWin32_Init(window_)) {
            Shutdown();
            return false;
        }
        win32Initialized_ = true;
        if (!ImGui_ImplDX11_Init(device_, context_)) {
            Shutdown();
            return false;
        }
        dx11Initialized_ = true;
        return true;
    }

    bool ResizeIfNeeded() {
        RECT rectangle{};
        if (!GetClientRect(window_, &rectangle)) return false;
        const UINT width = static_cast<UINT>(rectangle.right - rectangle.left);
        const UINT height = static_cast<UINT>(rectangle.bottom - rectangle.top);
        if (width == 0 || height == 0) return true;
        if (width == width_ && height == height_) return true;
        ReleaseCom(renderTarget_);
        if (FAILED(swapChain_->ResizeBuffers(0, width, height, DXGI_FORMAT_UNKNOWN, 0))) return false;
        width_ = width;
        height_ = height;
        return CreateRenderTarget();
    }

    HRESULT Render(const OverlayState& source) {
        std::vector<Recommendation> recommendations;
        std::vector<std::string> notifications;
        bool connected = false;
        bool stale = true;
        {
            auto& state = const_cast<OverlayState&>(source);
            std::scoped_lock lock(state.mutex);
            recommendations = state.recommendations;
            notifications = state.notifications;
            connected = state.connected;
            stale = state.updatedAt == Clock::time_point{} ||
                Clock::now() - state.updatedAt > kRecommendationTtl;
        }
        if (stale) recommendations.clear();

        ImGui_ImplDX11_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();
        Draw(recommendations, notifications, connected);
        ImGui::Render();

        const float key = protectedWindow_ ? 1.0F / 255.0F : 0.0F;
        const float clear[] = {key, 0.0F, key, protectedWindow_ ? 1.0F : 0.0F};
        context_->OMSetRenderTargets(1, &renderTarget_, nullptr);
        context_->ClearRenderTargetView(renderTarget_, clear);
        ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
        return swapChain_->Present(1, 0);
    }

    void Shutdown() {
        if (dx11Initialized_) ImGui_ImplDX11_Shutdown();
        if (win32Initialized_) ImGui_ImplWin32_Shutdown();
        if (contextCreated_) ImGui::DestroyContext();
        dx11Initialized_ = false;
        win32Initialized_ = false;
        contextCreated_ = false;
        ReleaseCom(renderTarget_);
        ReleaseCom(context_);
        ReleaseCom(device_);
        ReleaseCom(swapChain_);
        window_ = nullptr;
    }

private:
    HRESULT CreateDeviceAndSwapChain(
        DXGI_SWAP_CHAIN_DESC& description,
        const D3D_FEATURE_LEVEL (&levels)[2],
        D3D_DRIVER_TYPE driverType,
        D3D_FEATURE_LEVEL& selected) {
        return D3D11CreateDeviceAndSwapChain(
            nullptr, driverType, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            levels, static_cast<UINT>(std::size(levels)), D3D11_SDK_VERSION, &description,
            &swapChain_, &device_, &selected, &context_);
    }

    bool CreateRenderTarget() {
        ID3D11Texture2D* backBuffer = nullptr;
        if (FAILED(swapChain_->GetBuffer(0, IID_PPV_ARGS(&backBuffer)))) return false;
        const HRESULT result = device_->CreateRenderTargetView(backBuffer, nullptr, &renderTarget_);
        backBuffer->Release();
        return SUCCEEDED(result);
    }

    static void Draw(
        const std::vector<Recommendation>& recommendations,
        const std::vector<std::string>& notifications,
        bool connected) {
        ImGui::SetNextWindowPos(ImVec2(18.0F, 18.0F), ImGuiCond_Always);
        ImGui::SetNextWindowSize(ImVec2(460.0F, 0.0F), ImGuiCond_Always);
        ImGui::SetNextWindowBgAlpha(0.90F);
        constexpr ImGuiWindowFlags flags =
            ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
            ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing |
            ImGuiWindowFlags_NoNav | ImGuiWindowFlags_NoInputs;
        if (ImGui::Begin("Akagi Advanced Overlay", nullptr, flags)) {
            ImGui::TextColored(
                connected ? ImVec4(0.35F, 0.9F, 0.55F, 1.0F) : ImVec4(1.0F, 0.55F, 0.35F, 1.0F),
                connected ? "AKAGI-NG  |  CONNECTED" : "AKAGI-NG  |  RECONNECTING");
            ImGui::Separator();
            if (recommendations.empty()) {
                ImGui::TextDisabled("Waiting for current recommendation...");
            } else {
                int rank = 1;
                for (const auto& item : recommendations) {
                    const float confidence = std::clamp(item.confidence, 0.0F, 1.0F);
                    ImGui::Text(
                        "#%d  %s%s%s", rank, item.action.c_str(),
                        item.tile.empty() ? "" : "  ", item.tile.c_str());
                    ImGui::SameLine(285.0F);
                    ImGui::Text("%.1f%%", confidence * 100.0F);
                    ImGui::ProgressBar(confidence, ImVec2(420.0F, 7.0F), "");
                    if (!item.consumed.empty()) {
                        ImGui::TextDisabled("     %s", item.consumed.c_str());
                    }
                    ++rank;
                }
            }
            if (!notifications.empty()) {
                ImGui::Separator();
                ImGui::TextDisabled("%s", notifications.back().c_str());
            }
        }
        ImGui::End();
    }

    HWND window_ = nullptr;
    IDXGISwapChain* swapChain_ = nullptr;
    ID3D11Device* device_ = nullptr;
    ID3D11DeviceContext* context_ = nullptr;
    ID3D11RenderTargetView* renderTarget_ = nullptr;
    UINT width_ = 0;
    UINT height_ = 0;
    bool protectedWindow_ = false;
    bool contextCreated_ = false;
    bool win32Initialized_ = false;
    bool dx11Initialized_ = false;
};

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    if (ImGui::GetCurrentContext() &&
        ImGui_ImplWin32_WndProcHandler(window, message, wParam, lParam)) {
        return TRUE;
    }
    if (message == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    if (message == WM_NCHITTEST) return HTTRANSPARENT;
    return DefWindowProcW(window, message, wParam, lParam);
}

HWND CreateProtectedWindow(HINSTANCE instance, bool captureProtection) {
    WNDCLASSEXW definition{};
    definition.cbSize = sizeof(definition);
    definition.style = CS_HREDRAW | CS_VREDRAW;
    definition.lpfnWndProc = WindowProcedure;
    definition.hInstance = instance;
    definition.lpszClassName = kWindowClass;
    RegisterClassExW(&definition);

    const int width = 500;
    const int height = 300;
    const int x = GetSystemMetrics(SM_CXSCREEN) - width - 24;
    const int y = 80;
    HWND window = CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        kWindowClass, kWindowTitle, WS_POPUP, x, y, width, height, nullptr, nullptr, instance, nullptr);
    if (!window) return nullptr;
    SetLayeredWindowAttributes(window, RGB(1, 0, 1), 255, LWA_COLORKEY);
    if (captureProtection && !SetWindowDisplayAffinity(window, kExcludeFromCapture)) {
        std::cerr << "SetWindowDisplayAffinity failed: " << GetLastError() << '\n';
    }
    ShowWindow(window, SW_SHOWNOACTIVATE);
    UpdateWindow(window);
    return window;
}

bool ParentIsAlive(HANDLE parent) {
    return !parent || WaitForSingleObject(parent, 0) == WAIT_TIMEOUT;
}

bool EndWasPressed() {
    return (GetAsyncKeyState(VK_END) & 1) != 0;
}

}  // namespace

int main(int argc, char** argv) {
    SetConsoleOutputCP(CP_UTF8);
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    const Options options = ParseOptions(argc, argv);
    HANDLE parent = options.parentPid
        ? OpenProcess(SYNCHRONIZE, FALSE, options.parentPid)
        : nullptr;
    if (options.parentPid != 0 && !parent) {
        std::cerr << "Parent process is unavailable.\n";
        return 3;
    }

    const bool protectedHost = options.host != "discord";
    HINSTANCE instance = GetModuleHandleW(nullptr);
    HWND ownedWindow = protectedHost ? CreateProtectedWindow(instance, options.captureProtection) : nullptr;
    if (protectedHost && !ownedWindow) {
        std::cerr << "Failed to create protected overlay window: " << GetLastError() << '\n';
        if (parent) CloseHandle(parent);
        return 2;
    }
    if (!protectedHost && options.captureProtection) {
        std::cerr << "Discord owns its overlay HWND; capture protection cannot be applied by Akagi.\n";
    }
    if (options.selfTest) {
        std::cerr << "[self-test] window created\n";
        DWORD affinity = 0;
        const bool affinityRead =
            ownedWindow && GetWindowDisplayAffinity(ownedWindow, &affinity) != FALSE;
        OverlayState probeState;
        bool parserPassed = false;
        try {
            ApplyRecommendations(
                probeState,
                nlohmann::json::parse(
                    R"({"recommendations":[{"action":"discard","tile":"5m","confidence":0.75}]})"));
            probeState.connected = true;
            parserPassed = probeState.recommendations.size() == 1;
        } catch (const std::exception& error) {
            std::cerr << "[self-test] parser error: " << error.what() << '\n';
        }
        Renderer probeRenderer;
        std::cerr << "[self-test] initializing renderer\n";
        const bool rendererInitialized =
            ownedWindow && probeRenderer.Initialize(ownedWindow, true);
        std::cerr << "[self-test] renderer initialized=" << rendererInitialized << '\n';
        const bool frameRendered =
            rendererInitialized && SUCCEEDED(probeRenderer.Render(probeState));
        std::cerr << "[self-test] frame rendered=" << frameRendered << '\n';
        probeRenderer.Shutdown();
        std::cerr << "[self-test] renderer shutdown\n";
        std::cout << "{\"window\":" << (ownedWindow ? "true" : "false")
                  << ",\"affinity_read\":" << (affinityRead ? "true" : "false")
                  << ",\"affinity\":" << affinity
                  << ",\"parser\":" << (parserPassed ? "true" : "false")
                  << ",\"renderer\":" << (rendererInitialized ? "true" : "false")
                  << ",\"frame\":" << (frameRendered ? "true" : "false") << "}\n";
        if (ownedWindow) DestroyWindow(ownedWindow);
        std::cerr << "[self-test] window destroyed\n";
        if (parent) CloseHandle(parent);
        return ownedWindow && parserPassed && rendererInitialized && frameRendered &&
                (!options.captureProtection || affinity == kExcludeFromCapture)
            ? 0
            : 4;
    }

    OverlayState state;
    SseClient sse(options.sseUrl, state);
    sse.Start();

    bool running = true;
    while (running && ParentIsAlive(parent)) {
        HWND target = ownedWindow;
        while (!protectedHost && !target && running && ParentIsAlive(parent)) {
            target = FindWindowA("Chrome_WidgetWin_1", "Discord Overlay");
            if (!target) {
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
                running = !EndWasPressed();
            }
        }
        if (!running || !ParentIsAlive(parent)) break;

        Renderer renderer;
        if (!renderer.Initialize(target, protectedHost)) {
            std::cerr << "Renderer initialization failed; retrying.\n";
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }

        while (running && ParentIsAlive(parent) && IsWindow(target)) {
            MSG message{};
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                if (message.message == WM_QUIT) running = false;
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
            if (EndWasPressed()) running = false;
            if (!running || !renderer.ResizeIfNeeded()) break;
            if (FAILED(renderer.Render(state))) break;
        }
        renderer.Shutdown();
        if (!protectedHost && running) {
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        } else {
            break;
        }
    }

    sse.Stop();
    if (ownedWindow) DestroyWindow(ownedWindow);
    if (parent) CloseHandle(parent);
    return 0;
}
