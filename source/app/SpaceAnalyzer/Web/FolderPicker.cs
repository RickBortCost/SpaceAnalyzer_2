using System.Runtime.InteropServices;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Web;

/// <summary>
/// The Windows "Select folder" dialog (IFileOpenDialog with FOS_PICKFOLDERS), shown by the local service
/// over the application window. One dialog at a time; null when the operator cancels.
/// </summary>
public static class FolderPicker
{
    static readonly SemaphoreSlim Open = new(1, 1);

    public static Task<string?> Pick(string title, string? initial)
    {
        if (!OperatingSystem.IsWindows()) throw new UserError("Selezione della cartella disponibile solo su Windows: scrivere il percorso");
        if (!Open.Wait(0)) throw new UserError("La finestra di selezione della cartella è già aperta");
        var done = new TaskCompletionSource<string?>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            try { done.SetResult(Show(title, initial)); }
            catch (Exception exc) { done.SetException(exc); }
            finally { Open.Release(); }
        }) { IsBackground = true, Name = "folder-picker" };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        return done.Task;
    }

    const uint PickFolders = 0x20, ForceFileSystem = 0x40, PathMustExist = 0x800;
    const uint FileSystemPath = 0x80058000;
    const int Cancelled = unchecked((int)0x800704C7);

    static string? Show(string title, string? initial)
    {
        var dialog = (IFileOpenDialog)new FileOpenDialog();
        try
        {
            dialog.GetOptions(out var options);
            dialog.SetOptions(options | PickFolders | ForceFileSystem | PathMustExist);
            dialog.SetTitle(title);
            if (!string.IsNullOrEmpty(initial) && Directory.Exists(initial)
                && SHCreateItemFromParsingName(initial, IntPtr.Zero, typeof(IShellItem).GUID, out var folder) == 0)
            {
                dialog.SetFolder(folder);
                Marshal.ReleaseComObject(folder);
            }
            var result = dialog.Show(GetForegroundWindow());
            if (result == Cancelled) return null;
            Marshal.ThrowExceptionForHR(result);
            dialog.GetResult(out var item);
            try
            {
                item.GetDisplayName(FileSystemPath, out var name);
                try { return Marshal.PtrToStringUni(name); }
                finally { Marshal.FreeCoTaskMem(name); }
            }
            finally { Marshal.ReleaseComObject(item); }
        }
        finally { Marshal.ReleaseComObject(dialog); }
    }

    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
    static extern int SHCreateItemFromParsingName(string path, IntPtr bindContext, [MarshalAs(UnmanagedType.LPStruct)] Guid riid, out IShellItem item);

    [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
    class FileOpenDialog { }

    // IModalWindow + IFileDialog methods in vtable order, up to the last one used (GetResult).
    [ComImport, Guid("d57c7288-d4ad-4768-be02-9d969532d960"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IFileOpenDialog
    {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint count, IntPtr specs);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem item);
        void SetFolder(IShellItem item);
        void GetFolder(out IShellItem item);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void GetResult(out IShellItem item);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItem
    {
        void BindToHandler(IntPtr bindContext, ref Guid handler, ref Guid riid, out IntPtr result);
        void GetParent(out IShellItem parent);
        void GetDisplayName(uint form, out IntPtr name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem other, uint hint, out int order);
    }
}
