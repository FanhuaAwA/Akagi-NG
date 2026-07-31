cmake_minimum_required(VERSION 3.24)

project(AkagiAdvancedOverlay VERSION 1.1.0 LANGUAGES CXX)

if(NOT WIN32)
  message(FATAL_ERROR "Akagi Advanced Overlay is only supported on Windows.")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(FetchContent)

FetchContent_Declare(
  imgui
  GIT_REPOSITORY https://github.com/ocornut/imgui.git
  GIT_TAG v1.92.8
  GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(imgui)

FetchContent_Declare(
  nlohmann_json
  GIT_REPOSITORY https://github.com/nlohmann/json.git
  GIT_TAG v3.12.0
  GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(nlohmann_json)

add_library(akagi_imgui STATIC
  ${imgui_SOURCE_DIR}/imgui.cpp
  ${imgui_SOURCE_DIR}/imgui_draw.cpp
  ${imgui_SOURCE_DIR}/imgui_tables.cpp
  ${imgui_SOURCE_DIR}/imgui_widgets.cpp
  ${imgui_SOURCE_DIR}/backends/imgui_impl_dx11.cpp
  ${imgui_SOURCE_DIR}/backends/imgui_impl_win32.cpp
)

target_include_directories(akagi_imgui PUBLIC
  ${imgui_SOURCE_DIR}
  ${imgui_SOURCE_DIR}/backends
)

target_link_libraries(akagi_imgui PUBLIC d3d11 dxgi dwmapi)

add_executable(AkagiAdvancedOverlay src/main.cpp)

target_link_libraries(AkagiAdvancedOverlay PRIVATE
  akagi_imgui
  nlohmann_json::nlohmann_json
  winhttp
  user32
)

if(MSVC)
  target_compile_options(AkagiAdvancedOverlay PRIVATE /W4 /permissive- /utf-8)
  target_compile_definitions(AkagiAdvancedOverlay PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    UNICODE
    _UNICODE
  )
endif()
