using System;
using System.Drawing;
using System.Windows.Forms;

namespace SketchQuickTools
{
    internal static class NoteDialog
    {
        internal static string Read(IntPtr owner, string component)
        {
            using (var dialog = new Form())
            {
                dialog.Text = "添加零件批注";
                dialog.StartPosition = FormStartPosition.CenterParent;
                dialog.FormBorderStyle = FormBorderStyle.FixedDialog;
                dialog.MinimizeBox = false;
                dialog.MaximizeBox = false;
                dialog.ShowInTaskbar = false;
                dialog.AutoScaleMode = AutoScaleMode.Font;
                dialog.ClientSize = new Size(440, 226);

                var prompt = new Label { Text = "批注对象  " + component, AutoEllipsis = true, Dock = DockStyle.Fill };
                var input = new TextBox { Multiline = true, AcceptsReturn = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill };
                var create = new Button { Text = "添加", DialogResult = DialogResult.OK, Enabled = false, AutoSize = true };
                var cancel = new Button { Text = "取消", DialogResult = DialogResult.Cancel, AutoSize = true };
                var buttons = new FlowLayoutPanel { FlowDirection = FlowDirection.RightToLeft, Dock = DockStyle.Fill };
                buttons.Controls.Add(cancel);
                buttons.Controls.Add(create);
                var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(12), ColumnCount = 1, RowCount = 3 };
                layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 24));
                layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
                layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
                layout.Controls.Add(prompt, 0, 0);
                layout.Controls.Add(input, 0, 1);
                layout.Controls.Add(buttons, 0, 2);
                dialog.Controls.Add(layout);
                dialog.CancelButton = cancel;
                input.TextChanged += (sender, args) => create.Enabled = !string.IsNullOrWhiteSpace(input.Text);
                dialog.Shown += (sender, args) => input.Focus();
                DialogResult result = owner == IntPtr.Zero ? dialog.ShowDialog() : dialog.ShowDialog(new OwnerWindow(owner));
                return result == DialogResult.OK ? input.Text.Trim() : null;
            }
        }

        private sealed class OwnerWindow : IWin32Window
        {
            internal OwnerWindow(IntPtr handle) { Handle = handle; }

            public IntPtr Handle { get; private set; }
        }
    }
}
