import { lstat, readdir } from 'node:fs/promises';
import { join } from 'node:path';

export interface ResourceStatus {
  lib: boolean;
  models: boolean;
  missingCritical: string[];
  missingOptional: string[];
}

export interface ResourceCheckerOptions {
  platform?: NodeJS.Platform;
}

/**
 * Performs only a small, bounded availability check for resources that the UI
 * needs to describe before the Python backend starts. It intentionally does
 * not enumerate or hash the packaged application tree.
 */
export class ResourceChecker {
  private readonly platform: NodeJS.Platform;

  constructor(
    private readonly projectRoot: string,
    options: ResourceCheckerOptions = {},
  ) {
    this.platform = options.platform ?? process.platform;
  }

  public async check(): Promise<ResourceStatus> {
    const [lib, models] = await Promise.all([
      this.hasExpectedLibraries(join(this.projectRoot, 'lib')),
      this.hasModel(join(this.projectRoot, 'models')),
    ]);
    return {
      lib,
      models,
      missingCritical: lib ? [] : ['lib'],
      missingOptional: models ? [] : ['models'],
    };
  }

  private async hasExpectedLibraries(libDir: string): Promise<boolean> {
    const extension = this.platform === 'win32' ? 'pyd' : 'so';
    return (
      (await this.isRegularFile(join(libDir, `libriichi.${extension}`))) &&
      (await this.isRegularFile(join(libDir, `libriichi3p.${extension}`)))
    );
  }

  private async hasModel(modelsDir: string): Promise<boolean> {
    try {
      const entries = await readdir(modelsDir, { withFileTypes: true });
      return entries.some((entry) => entry.isFile() && entry.name.toLowerCase().endsWith('.pth'));
    } catch {
      return false;
    }
  }

  private async isRegularFile(path: string): Promise<boolean> {
    try {
      return (await lstat(path)).isFile();
    } catch {
      return false;
    }
  }
}
