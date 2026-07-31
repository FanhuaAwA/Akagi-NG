import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';

function buildBackend() {
  try {
    const projectRoot = resolve(__dirname, '..');
    const backendDir = join(projectRoot, 'akagi_backend');
    const buildScript = join(backendDir, 'scripts', 'build_backend.py');

    console.log('🔍 Identifying Python executable...');

    // Try local .venv first (for local development convenience)
    let pythonPath = 'python'; // Default to system PATH
    const venvPythonExec =
      process.platform === 'win32'
        ? join(backendDir, '.venv', 'Scripts', 'python.exe')
        : join(backendDir, '.venv', 'bin', 'python');

    if (existsSync(venvPythonExec)) {
      pythonPath = venvPythonExec;
      console.log(`✅ Using virtual environment: ${pythonPath}`);
    } else {
      console.log('ℹ️ Virtual environment not found, falling back to system "python"');
    }

    console.log(`🚀 Running backend build script: ${buildScript}`);

    const result = spawnSync(pythonPath, [buildScript], {
      cwd: projectRoot,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
      },
      stdio: 'inherit',
      shell: false,
    });

    if (result.status !== 0) {
      console.error(`❌ Backend build failed with exit code ${result.status}`);
      process.exit(result.status || 1);
    }

    console.log('✅ Backend build process completed successfully.');
  } catch (error) {
    console.error('❌ An unexpected error occurred during backend build:', error);
    process.exit(1);
  }
}

buildBackend();
