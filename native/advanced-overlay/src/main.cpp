#include <windows.h>
#include <windowsx.h>
#include <d3d11.h>
#include <dxgi.h>
#include <winhttp.h>
#include <wincodec.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
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
    std::string locale = "zh-CN";
    std::string tileRoot;
    std::string stateRoot;
    std::string snapshotPath;
    bool captureProtection = true;
    bool selfTest = false;
    DWORD parentPid = 0;
};

struct SimCandidate {
    std::string tile;
    float confidence = 0.0F;
};

struct Recommendation {
    std::string action;
    std::string tile;
    std::string consumed;
    std::vector<std::string> consumedTiles;
    std::vector<SimCandidate> simCandidates;
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
        if (const auto value = read("--locale=")) result.locale = *value;
        if (const auto value = read("--tiles=")) result.tileRoot = *value;
        if (const auto value = read("--state=")) result.stateRoot = *value;
        if (const auto value = read("--snapshot=")) result.snapshotPath = *value;
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

std::string WideToUtf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), nullptr, 0,
        nullptr, nullptr);
    if (size <= 0) return {};
    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), result.data(),
        size, nullptr, nullptr);
    return result;
}

std::string ExecutableDirectory() {
    std::wstring path(32768, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (length == 0 || length >= path.size()) return {};
    path.resize(length);
    return WideToUtf8(std::filesystem::path(path).parent_path().wstring());
}

class ComApartment {
public:
    ComApartment() : result_(CoInitializeEx(nullptr, COINIT_MULTITHREADED)) {}
    ~ComApartment() {
        if (SUCCEEDED(result_)) CoUninitialize();
    }
    bool Ready() const { return SUCCEEDED(result_) || result_ == RPC_E_CHANGED_MODE; }

private:
    HRESULT result_;
};

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

std::vector<std::string> ParseTiles(const nlohmann::json& value) {
    std::vector<std::string> result;
    if (!value.is_array()) return result;
    for (const auto& item : value) {
        if (item.is_string()) result.push_back(item.get<std::string>());
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
                recommendation.consumedTiles = ParseTiles(*consumed);
            }
            if (const auto candidates = item.find("sim_candidates");
                candidates != item.end() && candidates->is_array()) {
                for (const auto& candidate : *candidates) {
                    if (!candidate.is_object()) continue;
                    SimCandidate parsedCandidate;
                    parsedCandidate.tile = candidate.value("tile", "");
                    parsedCandidate.confidence = candidate.value("confidence", 0.0F);
                    if (!parsedCandidate.tile.empty()) {
                        recommendation.simCandidates.push_back(std::move(parsedCandidate));
                    }
                }
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
            L"Akagi-NG/1.1.1", WINHTTP_ACCESS_TYPE_NO_PROXY, WINHTTP_NO_PROXY_NAME,
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

    bool Initialize(
        HWND window,
        bool protectedWindow,
        const std::string& tileRoot,
        const std::string& locale,
        const std::string& stateRoot) {
        Shutdown();
        window_ = window;
        protectedWindow_ = protectedWindow;
        tileRoot_ = tileRoot;
        locale_ = locale;
        iniPath_ = stateRoot.empty() ? std::string{} : stateRoot + "-discord.ini";
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
        ConfigureStyle();
        ConfigureFonts();
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
        const HRESULT wicResult = CoCreateInstance(
            CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&wicFactory_));
        if (FAILED(wicResult) || !GetTileTexture("5m")) {
            std::cerr << "Failed to initialize native mahjong tile resources.\n";
            Shutdown();
            return false;
        }
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

    HRESULT Render(const OverlayState& source, const std::string& snapshotPath = {}) {
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
        if (!snapshotPath.empty()) {
            const HRESULT snapshotResult = SaveSnapshot(snapshotPath);
            if (FAILED(snapshotResult)) {
                std::cerr << "Snapshot export failed: 0x" << std::hex
                          << static_cast<unsigned long>(snapshotResult) << std::dec << '\n';
                return snapshotResult;
            }
        }
        return swapChain_->Present(1, 0);
    }

    void Shutdown() {
        if (dx11Initialized_) ImGui_ImplDX11_Shutdown();
        if (win32Initialized_) ImGui_ImplWin32_Shutdown();
        if (contextCreated_) ImGui::DestroyContext();
        dx11Initialized_ = false;
        win32Initialized_ = false;
        contextCreated_ = false;
        for (auto& [_, texture] : tileTextures_) ReleaseCom(texture.view);
        tileTextures_.clear();
        ReleaseCom(wicFactory_);
        ReleaseCom(renderTarget_);
        ReleaseCom(context_);
        ReleaseCom(device_);
        ReleaseCom(swapChain_);
        window_ = nullptr;
    }

private:
    struct TileTexture {
        ID3D11ShaderResourceView* view = nullptr;
        UINT width = 0;
        UINT height = 0;
    };

    struct ActionStyle {
        ImVec4 accent;
        ImVec4 accentDark;
    };

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

    HRESULT SaveSnapshot(const std::string& path) {
        ID3D11Texture2D* backBuffer = nullptr;
        ID3D11Texture2D* staging = nullptr;
        IWICStream* stream = nullptr;
        IWICBitmapEncoder* encoder = nullptr;
        IWICBitmapFrameEncode* frame = nullptr;
        IPropertyBag2* properties = nullptr;
        D3D11_MAPPED_SUBRESOURCE mapped{};

        HRESULT result = swapChain_->GetBuffer(0, IID_PPV_ARGS(&backBuffer));
        D3D11_TEXTURE2D_DESC description{};
        if (SUCCEEDED(result)) {
            backBuffer->GetDesc(&description);
            description.Usage = D3D11_USAGE_STAGING;
            description.BindFlags = 0;
            description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            description.MiscFlags = 0;
            result = device_->CreateTexture2D(&description, nullptr, &staging);
        }
        if (SUCCEEDED(result)) {
            context_->CopyResource(staging, backBuffer);
            result = context_->Map(staging, 0, D3D11_MAP_READ, 0, &mapped);
        }
        if (SUCCEEDED(result)) result = wicFactory_->CreateStream(&stream);
        if (SUCCEEDED(result)) {
            result = stream->InitializeFromFilename(Utf8ToWide(path).c_str(), GENERIC_WRITE);
        }
        if (SUCCEEDED(result)) {
            result = wicFactory_->CreateEncoder(GUID_ContainerFormatPng, nullptr, &encoder);
        }
        if (SUCCEEDED(result)) result = encoder->Initialize(stream, WICBitmapEncoderNoCache);
        if (SUCCEEDED(result)) result = encoder->CreateNewFrame(&frame, &properties);
        if (SUCCEEDED(result)) result = frame->Initialize(properties);
        if (SUCCEEDED(result)) result = frame->SetSize(description.Width, description.Height);
        WICPixelFormatGUID format = GUID_WICPixelFormat32bppBGRA;
        if (SUCCEEDED(result)) result = frame->SetPixelFormat(&format);
        if (SUCCEEDED(result) && !IsEqualGUID(format, GUID_WICPixelFormat32bppBGRA)) {
            result = WINCODEC_ERR_UNSUPPORTEDPIXELFORMAT;
        }
        std::vector<BYTE> bgra(
            static_cast<std::size_t>(description.Width) *
            static_cast<std::size_t>(description.Height) * 4U);
        if (SUCCEEDED(result)) {
            const auto* source = static_cast<const BYTE*>(mapped.pData);
            for (UINT y = 0; y < description.Height; ++y) {
                const BYTE* sourceRow = source + static_cast<std::size_t>(y) * mapped.RowPitch;
                BYTE* targetRow =
                    bgra.data() + static_cast<std::size_t>(y) * description.Width * 4U;
                for (UINT x = 0; x < description.Width; ++x) {
                    targetRow[x * 4U + 0U] = sourceRow[x * 4U + 2U];
                    targetRow[x * 4U + 1U] = sourceRow[x * 4U + 1U];
                    targetRow[x * 4U + 2U] = sourceRow[x * 4U + 0U];
                    targetRow[x * 4U + 3U] = sourceRow[x * 4U + 3U];
                }
            }
        }
        if (SUCCEEDED(result)) {
            result = frame->WritePixels(
                description.Height, description.Width * 4U,
                static_cast<UINT>(bgra.size()), bgra.data());
        }
        if (SUCCEEDED(result)) result = frame->Commit();
        if (SUCCEEDED(result)) result = encoder->Commit();

        if (mapped.pData) context_->Unmap(staging, 0);
        ReleaseCom(properties);
        ReleaseCom(frame);
        ReleaseCom(encoder);
        ReleaseCom(stream);
        ReleaseCom(staging);
        ReleaseCom(backBuffer);
        return result;
    }

    void ConfigureStyle() {
        ImGui::StyleColorsDark();
        ImGuiIO& io = ImGui::GetIO();
        io.IniFilename = protectedWindow_ || iniPath_.empty() ? nullptr : iniPath_.c_str();
        ImGuiStyle& style = ImGui::GetStyle();
        style.WindowRounding = 18.0F;
        style.ChildRounding = 18.0F;
        style.FrameRounding = 10.0F;
        style.WindowBorderSize = 0.0F;
        style.ChildBorderSize = 1.0F;
        style.WindowPadding = ImVec2(12.0F, 12.0F);
        style.ItemSpacing = ImVec2(8.0F, 8.0F);
        style.Colors[ImGuiCol_WindowBg] = ImVec4(0.02F, 0.02F, 0.03F, 0.25F);
        style.Colors[ImGuiCol_ChildBg] = ImVec4(0.035F, 0.035F, 0.045F, 0.92F);
        style.Colors[ImGuiCol_Border] = ImVec4(1.0F, 1.0F, 1.0F, 0.12F);
        style.Colors[ImGuiCol_ResizeGrip] = ImVec4(0.96F, 0.42F, 0.68F, 0.65F);
        style.Colors[ImGuiCol_ResizeGripHovered] = ImVec4(0.96F, 0.42F, 0.68F, 0.9F);
        style.Colors[ImGuiCol_ResizeGripActive] = ImVec4(0.96F, 0.42F, 0.68F, 1.0F);
    }

    void ConfigureFonts() {
        ImGuiIO& io = ImGui::GetIO();
        const char* path = "C:/Windows/Fonts/segoeui.ttf";
        const ImWchar* ranges = io.Fonts->GetGlyphRangesDefault();
        if (locale_.starts_with("zh")) {
            path = "C:/Windows/Fonts/msyh.ttc";
            ranges = io.Fonts->GetGlyphRangesChineseFull();
        } else if (locale_.starts_with("ja")) {
            path = "C:/Windows/Fonts/YuGothR.ttc";
            ranges = io.Fonts->GetGlyphRangesJapanese();
        }
        bodyFont_ = io.Fonts->AddFontFromFileTTF(path, 22.0F, nullptr, ranges);
        actionFont_ = io.Fonts->AddFontFromFileTTF(path, 40.0F, nullptr, ranges);
        if (!bodyFont_ || !actionFont_) {
            bodyFont_ = io.Fonts->AddFontDefault();
            actionFont_ = bodyFont_;
        }
    }

    TileTexture* GetTileTexture(const std::string& tile) {
        if (const auto found = tileTextures_.find(tile); found != tileTextures_.end()) {
            return &found->second;
        }
        if (tile.empty() ||
            std::any_of(tile.begin(), tile.end(), [](unsigned char character) {
                return !std::isalnum(character);
            })) {
            return nullptr;
        }

        const std::filesystem::path path =
            std::filesystem::path(Utf8ToWide(tileRoot_)) / (Utf8ToWide(tile) + L".png");
        IWICBitmapDecoder* decoder = nullptr;
        IWICBitmapFrameDecode* frame = nullptr;
        IWICFormatConverter* converter = nullptr;
        HRESULT result = wicFactory_->CreateDecoderFromFilename(
            path.c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &decoder);
        if (SUCCEEDED(result)) result = decoder->GetFrame(0, &frame);
        if (SUCCEEDED(result)) result = wicFactory_->CreateFormatConverter(&converter);
        if (SUCCEEDED(result)) {
            result = converter->Initialize(
                frame, GUID_WICPixelFormat32bppRGBA, WICBitmapDitherTypeNone, nullptr, 0.0,
                WICBitmapPaletteTypeCustom);
        }
        UINT width = 0;
        UINT height = 0;
        if (SUCCEEDED(result)) result = converter->GetSize(&width, &height);
        std::vector<std::uint8_t> pixels(
            static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4U);
        if (SUCCEEDED(result)) {
            result = converter->CopyPixels(
                nullptr, width * 4U, static_cast<UINT>(pixels.size()), pixels.data());
        }

        ID3D11Texture2D* texture = nullptr;
        ID3D11ShaderResourceView* view = nullptr;
        if (SUCCEEDED(result)) {
            D3D11_TEXTURE2D_DESC description{};
            description.Width = width;
            description.Height = height;
            description.MipLevels = 1;
            description.ArraySize = 1;
            description.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
            description.SampleDesc.Count = 1;
            description.Usage = D3D11_USAGE_IMMUTABLE;
            description.BindFlags = D3D11_BIND_SHADER_RESOURCE;
            const D3D11_SUBRESOURCE_DATA data{pixels.data(), width * 4U, 0};
            result = device_->CreateTexture2D(&description, &data, &texture);
        }
        if (SUCCEEDED(result)) {
            result = device_->CreateShaderResourceView(texture, nullptr, &view);
        }
        ReleaseCom(texture);
        ReleaseCom(converter);
        ReleaseCom(frame);
        ReleaseCom(decoder);
        if (FAILED(result) || !view) return nullptr;

        const auto [iterator, _] =
            tileTextures_.emplace(tile, TileTexture{view, width, height});
        return &iterator->second;
    }

    ActionStyle GetActionStyle(const std::string& action) const {
        if (action == "reach") return {{0.98F, 0.45F, 0.12F, 1.0F}, {0.86F, 0.15F, 0.15F, 1.0F}};
        if (action == "chi") return {{0.20F, 0.83F, 0.60F, 1.0F}, {0.09F, 0.55F, 0.30F, 1.0F}};
        if (action == "pon") return {{0.38F, 0.65F, 0.98F, 1.0F}, {0.31F, 0.28F, 0.86F, 1.0F}};
        if (action == "kan") return {{0.75F, 0.48F, 0.98F, 1.0F}, {0.76F, 0.20F, 0.88F, 1.0F}};
        if (action == "ron" || action == "tsumo") {
            return {{0.96F, 0.25F, 0.30F, 1.0F}, {0.62F, 0.05F, 0.14F, 1.0F}};
        }
        if (action == "nukidora") return {{0.96F, 0.45F, 0.70F, 1.0F}, {0.88F, 0.20F, 0.45F, 1.0F}};
        return {{0.62F, 0.62F, 0.68F, 1.0F}, {0.28F, 0.28F, 0.32F, 1.0F}};
    }

    std::string ActionLabel(const std::string& action) const {
        const bool simplified = locale_.starts_with("zh-CN") || locale_.starts_with("zh-Hans");
        const bool traditional = locale_.starts_with("zh-TW") || locale_.starts_with("zh-Hant");
        const bool japanese = locale_.starts_with("ja");
        if (simplified || traditional || japanese) {
            if (action == "reach") return simplified ? "立直" : "立直";
            if (action == "chi") return simplified ? "吃" : (traditional ? "吃" : "チー");
            if (action == "pon") return simplified ? "碰" : (traditional ? "碰" : "ポン");
            if (action == "kan") return simplified ? "杠" : (traditional ? "槓" : "カン");
            if (action == "ron") return simplified ? "荣和" : (traditional ? "榮和" : "ロン");
            if (action == "tsumo") return simplified ? "自摸" : (traditional ? "自摸" : "ツモ");
            if (action == "ryukyoku") return simplified ? "流局" : (traditional ? "流局" : "流局");
            if (action == "nukidora") return simplified ? "拔北" : (traditional ? "拔北" : "抜き北");
            if (action == "none") return simplified ? "跳过" : (traditional ? "跳過" : "スキップ");
            return simplified ? "打牌" : (traditional ? "打牌" : "打牌");
        }
        if (action == "reach") return "RIICHI";
        if (action == "chi") return "CHI";
        if (action == "pon") return "PON";
        if (action == "kan") return "KAN";
        if (action == "ron") return "RON";
        if (action == "tsumo") return "TSUMO";
        if (action == "ryukyoku") return "DRAW";
        if (action == "nukidora") return "KITA";
        if (action == "none") return "SKIP";
        return "DISCARD";
    }

    bool IsKnownAction(const std::string& action) const {
        static const std::vector<std::string> actions = {
            "reach", "chi", "pon", "kan", "ron", "tsumo", "ryukyoku", "nukidora", "none"};
        return std::find(actions.begin(), actions.end(), action) != actions.end();
    }

    void DrawTile(const std::string& tile, const ImVec2& size, float alpha = 1.0F) {
        if (TileTexture* texture = GetTileTexture(tile)) {
            ImGui::Dummy(size);
            const ImVec2 minimum = ImGui::GetItemRectMin();
            const ImVec2 maximum = ImGui::GetItemRectMax();
            const float thickness = std::max(2.0F, size.y * 0.045F);
            const float rounding = std::max(4.0F, size.x * 0.09F);
            ImDrawList* draw = ImGui::GetWindowDrawList();
            const ImU32 faceColor = IM_COL32(248, 248, 246, static_cast<int>(245.0F * alpha));
            const ImU32 edgeColor = IM_COL32(175, 177, 184, static_cast<int>(230.0F * alpha));
            draw->AddRectFilled(
                ImVec2(minimum.x, minimum.y + thickness), maximum, edgeColor, rounding);
            draw->AddRectFilled(
                minimum, ImVec2(maximum.x, maximum.y - thickness), faceColor, rounding);
            draw->AddRect(
                minimum, ImVec2(maximum.x, maximum.y - thickness),
                IM_COL32(205, 205, 210, static_cast<int>(230.0F * alpha)), rounding);
            draw->AddImage(
                ImTextureRef(texture->view),
                ImVec2(minimum.x + 2.0F, minimum.y + 2.0F),
                ImVec2(maximum.x - 2.0F, maximum.y - thickness - 2.0F),
                ImVec2(0, 0), ImVec2(1, 1),
                IM_COL32(255, 255, 255, static_cast<int>(255.0F * alpha)));
            return;
        }
        ImGui::Dummy(size);
        const ImVec2 minimum = ImGui::GetItemRectMin();
        const ImVec2 maximum = ImGui::GetItemRectMax();
        ImGui::GetWindowDrawList()->AddRectFilled(minimum, maximum, IM_COL32(245, 245, 245, 220), 6.0F);
        ImGui::GetWindowDrawList()->AddText(
            ImVec2(minimum.x + 4.0F, minimum.y + 4.0F), IM_COL32(35, 35, 40, 255), tile.c_str());
    }

    void DrawConfidence(float confidence, const ActionStyle& style, float diameter) {
        const float normalized = std::clamp(confidence, 0.0F, 1.0F);
        const ImVec2 cursor = ImGui::GetCursorScreenPos();
        const ImVec2 center(cursor.x + diameter * 0.5F, cursor.y + diameter * 0.5F);
        ImDrawList* draw = ImGui::GetWindowDrawList();
        const float radius = diameter * 0.5F - 5.0F;
        draw->AddCircle(center, radius, IM_COL32(70, 70, 78, 255), 64, 7.0F);
        constexpr float pi = 3.14159265358979323846F;
        draw->PathArcTo(
            center, radius, -pi * 0.5F, -pi * 0.5F + pi * 2.0F * normalized, 48);
        draw->PathStroke(ImGui::ColorConvertFloat4ToU32(style.accent), 0, 7.0F);
        char value[16]{};
        std::snprintf(value, sizeof(value), "%.0f%%", normalized * 100.0F);
        ImGui::PushFont(bodyFont_);
        const ImVec2 textSize = ImGui::CalcTextSize(value);
        draw->AddText(
            bodyFont_, bodyFont_->LegacySize,
            ImVec2(center.x - textSize.x * 0.5F, center.y - textSize.y * 0.5F),
            IM_COL32(232, 232, 238, 255), value);
        ImGui::PopFont();
        ImGui::Dummy(ImVec2(diameter, diameter));
    }

    void DrawRecommendationCard(
        const Recommendation& recommendation,
        int rank,
        float width,
        float height) {
        const ActionStyle style = GetActionStyle(recommendation.action);
        const float scale = std::clamp(height / 176.0F, 0.48F, 1.15F);
        const ImVec2 tileSize(80.0F * scale, 112.0F * scale);
        const float confidenceSize = 112.0F * scale;
        const std::string identifier = "recommendation-" + std::to_string(rank);
        ImGui::PushStyleVar(ImGuiStyleVar_ChildRounding, 22.0F * scale);
        ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.025F, 0.025F, 0.035F, 0.92F));
        ImGui::BeginChild(
            identifier.c_str(), ImVec2(width, height), ImGuiChildFlags_Borders,
            ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

        ImDrawList* draw = ImGui::GetWindowDrawList();
        const ImVec2 minimum = ImGui::GetWindowPos();
        const ImVec2 maximum(minimum.x + width, minimum.y + height);
        draw->AddRectFilled(
            ImVec2(minimum.x + 1.0F, minimum.y + 1.0F),
            ImVec2(minimum.x + 8.0F * scale, maximum.y - 1.0F),
            ImGui::ColorConvertFloat4ToU32(style.accent), 18.0F * scale);
        draw->AddRectFilledMultiColor(
            ImVec2(minimum.x + 8.0F * scale, minimum.y + 1.0F),
            ImVec2(maximum.x - 1.0F, maximum.y - 1.0F),
            ImGui::ColorConvertFloat4ToU32(ImVec4(
                style.accent.x, style.accent.y, style.accent.z, 0.12F)),
            ImGui::ColorConvertFloat4ToU32(ImVec4(0.04F, 0.04F, 0.055F, 0.10F)),
            ImGui::ColorConvertFloat4ToU32(ImVec4(0.04F, 0.04F, 0.055F, 0.10F)),
            ImGui::ColorConvertFloat4ToU32(ImVec4(
                style.accentDark.x, style.accentDark.y, style.accentDark.z, 0.12F)));

        const float labelWidth = 205.0F * scale;
        ImGui::SetCursorPos(ImVec2(18.0F * scale, height * 0.5F - actionFont_->LegacySize * 0.55F));
        ImGui::PushFont(actionFont_);
        ImGui::PushStyleColor(ImGuiCol_Text, style.accent);
        const std::string label = ActionLabel(recommendation.action);
        const ImVec2 labelSize = ImGui::CalcTextSize(label.c_str());
        ImGui::SetCursorPosX(18.0F * scale + std::max(0.0F, (labelWidth - labelSize.x) * 0.5F));
        ImGui::TextUnformatted(label.c_str());
        ImGui::PopStyleColor();
        ImGui::PopFont();

        const float dividerX = minimum.x + labelWidth + 24.0F * scale;
        draw->AddLine(
            ImVec2(dividerX, minimum.y + 28.0F * scale),
            ImVec2(dividerX, maximum.y - 28.0F * scale), IM_COL32(105, 105, 115, 170),
            1.0F);

        ImGui::SetCursorPos(ImVec2(labelWidth + 50.0F * scale, (height - tileSize.y) * 0.5F));
        ImGui::BeginGroup();
        if (!recommendation.simCandidates.empty()) {
            for (std::size_t index = 0; index < recommendation.simCandidates.size(); ++index) {
                if (index > 0) ImGui::SameLine(0.0F, 14.0F * scale);
                ImGui::BeginGroup();
                DrawTile(recommendation.simCandidates[index].tile, tileSize);
                if (recommendation.simCandidates.size() > 1) {
                    ImGui::SameLine(0.0F, 4.0F * scale);
                    ImGui::PushFont(bodyFont_);
                    ImGui::SetCursorPosY(
                        ImGui::GetCursorPosY() + tileSize.y * 0.5F - bodyFont_->LegacySize * 0.5F);
                    ImGui::Text(
                        "%.0f%%", recommendation.simCandidates[index].confidence * 100.0F);
                    ImGui::PopFont();
                }
                ImGui::EndGroup();
            }
        } else {
            std::string mainTile;
            if ((recommendation.action == "tsumo" || recommendation.action == "ron" ||
                 recommendation.action == "nukidora") &&
                !recommendation.tile.empty()) {
                mainTile = recommendation.tile;
            } else if (!IsKnownAction(recommendation.action)) {
                mainTile = recommendation.action;
            }
            if (!mainTile.empty()) {
                DrawTile(mainTile, tileSize);
                if (!recommendation.consumedTiles.empty()) ImGui::SameLine(0.0F, 12.0F * scale);
            }
            if (!recommendation.consumedTiles.empty() &&
                (recommendation.action == "chi" || recommendation.action == "pon" ||
                 recommendation.action == "kan")) {
                if (!recommendation.tile.empty()) {
                    DrawTile(recommendation.tile, tileSize);
                    ImGui::SameLine(0.0F, 10.0F * scale);
                    ImGui::PushFont(actionFont_);
                    ImGui::TextUnformatted(">");
                    ImGui::PopFont();
                    ImGui::SameLine(0.0F, 10.0F * scale);
                }
                std::vector<std::string> sorted = recommendation.consumedTiles;
                std::stable_sort(
                    sorted.begin(), sorted.end(), [](const std::string& left, const std::string& right) {
                        const int leftValue = left.empty() || !std::isdigit(left.front()) ? 99 : left.front() - '0';
                        const int rightValue =
                            right.empty() || !std::isdigit(right.front()) ? 99 : right.front() - '0';
                        return leftValue < rightValue;
                    });
                for (std::size_t index = 0; index < sorted.size(); ++index) {
                    if (index > 0) ImGui::SameLine(0.0F, 3.0F * scale);
                    DrawTile(sorted[index], tileSize);
                }
            }
        }
        ImGui::EndGroup();

        ImGui::SetCursorPos(ImVec2(width - confidenceSize - 22.0F * scale, (height - confidenceSize) * 0.5F));
        DrawConfidence(recommendation.confidence, style, confidenceSize);
        ImGui::EndChild();
        ImGui::PopStyleColor();
        ImGui::PopStyleVar();
    }

    void Draw(
        const std::vector<Recommendation>& recommendations,
        const std::vector<std::string>& notifications,
        bool connected) {
        const ImGuiIO& io = ImGui::GetIO();
        if (protectedWindow_) {
            ImGui::SetNextWindowPos(ImVec2(0, 0), ImGuiCond_Always);
            ImGui::SetNextWindowSize(io.DisplaySize, ImGuiCond_Always);
        } else {
            ImGui::SetNextWindowPos(ImVec2(18.0F, 18.0F), ImGuiCond_FirstUseEver);
            ImGui::SetNextWindowSize(ImVec2(640.0F, 360.0F), ImGuiCond_FirstUseEver);
            ImGui::SetNextWindowSizeConstraints(ImVec2(320.0F, 180.0F), ImVec2(1280.0F, 720.0F));
        }
        ImGui::SetNextWindowBgAlpha(protectedWindow_ ? 0.0F : 0.18F);
        ImGuiWindowFlags flags =
            ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoCollapse |
            ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse;
        if (protectedWindow_) {
            flags |= ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoResize |
                ImGuiWindowFlags_NoSavedSettings;
        }
        if (ImGui::Begin("Akagi Advanced Overlay", nullptr, flags)) {
            ImGui::PushFont(bodyFont_);
            ImGui::TextColored(
                connected ? ImVec4(0.35F, 0.9F, 0.55F, 1.0F) : ImVec4(1.0F, 0.55F, 0.35F, 1.0F),
                connected ? "AKAGI-NG  |  CONNECTED" : "AKAGI-NG  |  RECONNECTING");
            if (protectedWindow_) {
                ImGui::SameLine();
                ImGui::TextDisabled("| drag top area / resize edges");
            }
            ImGui::PopFont();

            ImVec2 available = ImGui::GetContentRegionAvail();
            if (recommendations.empty()) {
                ImGui::PushFont(bodyFont_);
                ImGui::SetCursorPosY(ImGui::GetCursorPosY() + available.y * 0.35F);
                ImGui::TextDisabled("Waiting for current recommendation...");
                ImGui::PopFont();
            } else {
                const int count = static_cast<int>(std::min<std::size_t>(3, recommendations.size()));
                const float gap = 10.0F;
                const float cardHeight = std::max(76.0F, (available.y - gap * (count - 1)) / count);
                for (int index = 0; index < count; ++index) {
                    DrawRecommendationCard(recommendations[index], index + 1, available.x, cardHeight);
                    if (index + 1 < count) ImGui::Dummy(ImVec2(0, gap));
                }
            }
            if (!notifications.empty()) {
                ImGui::PushFont(bodyFont_);
                ImGui::TextDisabled("%s", notifications.back().c_str());
                ImGui::PopFont();
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
    IWICImagingFactory* wicFactory_ = nullptr;
    std::unordered_map<std::string, TileTexture> tileTextures_;
    std::string tileRoot_;
    std::string locale_;
    std::string iniPath_;
    ImFont* bodyFont_ = nullptr;
    ImFont* actionFont_ = nullptr;
};

std::wstring gBoundsPath;

void SaveWindowBounds(HWND window) {
    if (gBoundsPath.empty() || !window) return;
    RECT rectangle{};
    if (!GetWindowRect(window, &rectangle)) return;
    const nlohmann::json data = {
        {"x", rectangle.left},
        {"y", rectangle.top},
        {"width", rectangle.right - rectangle.left},
        {"height", rectangle.bottom - rectangle.top},
    };
    std::ofstream stream(std::filesystem::path(gBoundsPath), std::ios::trunc);
    if (stream) stream << data.dump(2);
}

RECT LoadWindowBounds(const std::string& stateRoot) {
    const int defaultWidth = 640;
    const int defaultHeight = 360;
    const int defaultX = GetSystemMetrics(SM_CXSCREEN) - defaultWidth - 24;
    const int defaultY = 80;
    RECT result{defaultX, defaultY, defaultX + defaultWidth, defaultY + defaultHeight};
    if (stateRoot.empty()) return result;
    gBoundsPath = Utf8ToWide(stateRoot + "-protected.json");
    try {
        std::ifstream stream{std::filesystem::path(gBoundsPath)};
        if (!stream) return result;
        const nlohmann::json data = nlohmann::json::parse(stream);
        const int width = std::clamp(data.value("width", defaultWidth), 320, 1280);
        const int height = std::clamp(data.value("height", defaultHeight), 180, 720);
        const int x = data.value("x", defaultX);
        const int y = data.value("y", defaultY);
        result = {x, y, x + width, y + height};
    } catch (const std::exception& error) {
        std::cerr << "Overlay bounds state error: " << error.what() << '\n';
    }
    return result;
}

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    if (ImGui::GetCurrentContext() &&
        ImGui_ImplWin32_WndProcHandler(window, message, wParam, lParam)) {
        return TRUE;
    }
    if (message == WM_NCCALCSIZE && wParam == TRUE) return 0;
    if (message == WM_GETMINMAXINFO) {
        auto* info = reinterpret_cast<MINMAXINFO*>(lParam);
        info->ptMinTrackSize = {320, 180};
        info->ptMaxTrackSize = {1280, 720};
        return 0;
    }
    if (message == WM_EXITSIZEMOVE) {
        SaveWindowBounds(window);
        return 0;
    }
    if (message == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    if (message == WM_NCHITTEST) {
        RECT bounds{};
        GetWindowRect(window, &bounds);
        const POINT cursor{GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)};
        constexpr int edge = 10;
        const bool left = cursor.x < bounds.left + edge;
        const bool right = cursor.x >= bounds.right - edge;
        const bool top = cursor.y < bounds.top + edge;
        const bool bottom = cursor.y >= bounds.bottom - edge;
        if (top && left) return HTTOPLEFT;
        if (top && right) return HTTOPRIGHT;
        if (bottom && left) return HTBOTTOMLEFT;
        if (bottom && right) return HTBOTTOMRIGHT;
        if (left) return HTLEFT;
        if (right) return HTRIGHT;
        if (top) return HTTOP;
        if (bottom) return HTBOTTOM;
        if (cursor.y < bounds.top + 34) return HTCAPTION;
        return HTCLIENT;
    }
    return DefWindowProcW(window, message, wParam, lParam);
}

HWND CreateProtectedWindow(
    HINSTANCE instance,
    bool captureProtection,
    const std::string& stateRoot) {
    WNDCLASSEXW definition{};
    definition.cbSize = sizeof(definition);
    definition.style = CS_HREDRAW | CS_VREDRAW;
    definition.lpfnWndProc = WindowProcedure;
    definition.hInstance = instance;
    definition.lpszClassName = kWindowClass;
    RegisterClassExW(&definition);

    const RECT bounds = LoadWindowBounds(stateRoot);
    const int width = bounds.right - bounds.left;
    const int height = bounds.bottom - bounds.top;
    HWND window = CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        kWindowClass, kWindowTitle, WS_POPUP | WS_THICKFRAME, bounds.left, bounds.top, width, height,
        nullptr, nullptr, instance, nullptr);
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
    ComApartment com;
    if (!com.Ready()) {
        std::cerr << "Failed to initialize COM.\n";
        return 5;
    }
    Options options = ParseOptions(argc, argv);
    const std::string executableDirectory = ExecutableDirectory();
    if (options.tileRoot.empty()) {
        options.tileRoot = WideToUtf8(
            (std::filesystem::path(Utf8ToWide(executableDirectory)) / L"tiles").wstring());
    }
    if (options.stateRoot.empty()) {
        options.stateRoot = WideToUtf8(
            (std::filesystem::path(Utf8ToWide(executableDirectory)) / L"advanced-overlay").wstring());
    }
    HANDLE parent = options.parentPid
        ? OpenProcess(SYNCHRONIZE, FALSE, options.parentPid)
        : nullptr;
    if (options.parentPid != 0 && !parent) {
        std::cerr << "Parent process is unavailable.\n";
        return 3;
    }

    const bool protectedHost = options.host != "discord";
    HINSTANCE instance = GetModuleHandleW(nullptr);
    HWND ownedWindow = protectedHost
        ? CreateProtectedWindow(instance, options.captureProtection, options.stateRoot)
        : nullptr;
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
        RECT interactionBounds{};
        GetWindowRect(ownedWindow, &interactionBounds);
        const int interactionWidth = interactionBounds.right - interactionBounds.left;
        const LRESULT dragHit = SendMessageW(
            ownedWindow, WM_NCHITTEST, 0,
            MAKELPARAM(interactionBounds.left + interactionWidth / 2, interactionBounds.top + 20));
        const LRESULT resizeHit = SendMessageW(
            ownedWindow, WM_NCHITTEST, 0,
            MAKELPARAM(interactionBounds.right - 2, interactionBounds.bottom - 2));
        const bool interactionPassed =
            dragHit == HTCAPTION && resizeHit == HTBOTTOMRIGHT &&
            (GetWindowLongPtrW(ownedWindow, GWL_STYLE) & WS_THICKFRAME) != 0;
        OverlayState probeState;
        bool parserPassed = false;
        try {
            ApplyRecommendations(
                probeState,
                nlohmann::json::parse(
                    R"({"recommendations":[{"action":"reach","confidence":0.92,"sim_candidates":[{"tile":"8m","confidence":0.95},{"tile":"1m","confidence":0.05}]},{"action":"chi","tile":"7m","confidence":0.85,"consumed":["5m","6m"]},{"action":"W","confidence":0.63}]})"));
            probeState.connected = true;
            parserPassed =
                probeState.recommendations.size() == 3 &&
                probeState.recommendations[0].simCandidates.size() == 2 &&
                probeState.recommendations[1].consumedTiles.size() == 2;
        } catch (const std::exception& error) {
            std::cerr << "[self-test] parser error: " << error.what() << '\n';
        }
        Renderer probeRenderer;
        std::cerr << "[self-test] initializing renderer\n";
        const bool rendererInitialized =
            ownedWindow && probeRenderer.Initialize(
                ownedWindow, true, options.tileRoot, options.locale, options.stateRoot);
        std::cerr << "[self-test] renderer initialized=" << rendererInitialized << '\n';
        const bool frameRendered =
            rendererInitialized && SUCCEEDED(probeRenderer.Render(probeState, options.snapshotPath));
        std::cerr << "[self-test] frame rendered=" << frameRendered << '\n';
        probeRenderer.Shutdown();
        std::cerr << "[self-test] renderer shutdown\n";
        std::cout << "{\"window\":" << (ownedWindow ? "true" : "false")
                  << ",\"affinity_read\":" << (affinityRead ? "true" : "false")
                  << ",\"affinity\":" << affinity
                  << ",\"interaction\":" << (interactionPassed ? "true" : "false")
                  << ",\"parser\":" << (parserPassed ? "true" : "false")
                  << ",\"renderer\":" << (rendererInitialized ? "true" : "false")
                  << ",\"snapshot\":" << (!options.snapshotPath.empty() ? "true" : "false")
                  << ",\"frame\":" << (frameRendered ? "true" : "false") << "}\n";
        if (ownedWindow) DestroyWindow(ownedWindow);
        std::cerr << "[self-test] window destroyed\n";
        if (parent) CloseHandle(parent);
        return ownedWindow && interactionPassed && parserPassed && rendererInitialized && frameRendered &&
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
        if (!renderer.Initialize(
                target, protectedHost, options.tileRoot, options.locale, options.stateRoot)) {
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
    if (ownedWindow) {
        SaveWindowBounds(ownedWindow);
        DestroyWindow(ownedWindow);
    }
    if (parent) CloseHandle(parent);
    return 0;
}
