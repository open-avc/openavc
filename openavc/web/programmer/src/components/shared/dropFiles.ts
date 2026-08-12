/**
 * The files a drop is actually carrying, folder structure included.
 *
 * A dropped **folder** is not usable out of `dataTransfer.files`: the browser
 * reports one entry standing for the folder itself, with no contents, and the
 * `File` objects a drop produces carry an EMPTY `webkitRelativePath` — that
 * property is filled in only by an `<input webkitdirectory>` picker. The
 * structure comes out of `DataTransferItem.webkitGetAsEntry()` and a walk of
 * the directory reader, so the folder each file sat in has to be carried
 * alongside it rather than read back off it.
 *
 * Two details that look like nothing and are not:
 *  - The item list has to be claimed **before the first `await`**. A
 *    `DataTransfer` is emptied as soon as the drop handler returns, so an
 *    async walk that reaches back into it finds nothing.
 *  - `readEntries()` hands back a batch at a time (100 in Chromium) and
 *    signals the end with an empty batch. One call is not the directory, it is
 *    the first hundred files of it.
 */

export interface DroppedFile {
  file: File;
  /** Folder the file sat in, relative to what was dropped ("" at the top). */
  folder: string;
}

/** The subset of the non-standard FileSystem entry API this walk needs. */
interface FSEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (onOk: (f: File) => void, onErr?: (e: unknown) => void) => void;
  createReader?: () => {
    readEntries: (onOk: (entries: FSEntry[]) => void, onErr?: (e: unknown) => void) => void;
  };
}

type EntryItem = DataTransferItem & { webkitGetAsEntry?: () => FSEntry | null };

/** The folder half of a path like `room_map/map.css` — "" when there is none. */
export function folderOf(path: string): string {
  const cleaned = path.replace(/\\/g, "/");
  const cut = cleaned.lastIndexOf("/");
  return cut === -1 ? "" : cleaned.slice(0, cut);
}

export async function filesFromDataTransfer(dt: DataTransfer): Promise<DroppedFile[]> {
  const roots: FSEntry[] = [];
  for (const item of Array.from(dt.items ?? [])) {
    const entry = (item as EntryItem).webkitGetAsEntry?.();
    if (entry) roots.push(entry);
  }

  if (roots.length === 0) {
    // No filesystem entries: a plain file drop, or a browser without the
    // non-standard API. The flat list is all there is, and a folder picked by
    // an `<input webkitdirectory>` arrives here with its path filled in.
    return filesFromList(dt.files);
  }

  const out: DroppedFile[] = [];
  for (const root of roots) await walk(root, "", out);
  return out;
}

/** The same shape out of a file `<input>`, where the browser fills the path. */
export function filesFromList(list: FileList | File[] | null | undefined): DroppedFile[] {
  return Array.from(list ?? []).map((file) => ({
    file,
    folder: folderOf((file as File & { webkitRelativePath?: string }).webkitRelativePath || ""),
  }));
}

async function walk(entry: FSEntry, folder: string, out: DroppedFile[]): Promise<void> {
  if (entry.isFile && entry.file) {
    const file = await new Promise<File | null>((resolve) =>
      entry.file!((f) => resolve(f), () => resolve(null)),
    );
    // A file the browser refuses to hand over is skipped rather than failing
    // the whole drop: one unreadable file should not lose the other ninety.
    if (file) out.push({ file, folder });
    return;
  }
  if (entry.isDirectory && entry.createReader) {
    const child = folder ? `${folder}/${entry.name}` : entry.name;
    const reader = entry.createReader();
    for (;;) {
      const batch = await new Promise<FSEntry[]>((resolve) =>
        reader.readEntries((entries) => resolve(entries), () => resolve([])),
      );
      if (batch.length === 0) break;
      for (const e of batch) await walk(e, child, out);
    }
  }
}
