using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Media.Media3D;

namespace DubSync.Desktop.Services;

public static class FontScalingService
{
    private sealed class FontBaseline(double size)
    {
        public double Size { get; } = size;
    }

    private static readonly ConditionalWeakTable<DependencyObject, FontBaseline> Baselines = new();
    private static double _currentScale = 1.0;

    public static void Apply(DependencyObject root, double scale)
    {
        if (scale is < 0.8 or > 1.5) return;

        var elements = Enumerate(root).ToArray();
        var sizes = elements.Select(element => (Element: element, Baseline: GetBaseline(element))).ToArray();
        _currentScale = scale;
        foreach (var (element, baseline) in sizes)
            element.SetCurrentValue(TextElement.FontSizeProperty, baseline * scale);
    }

    public static void ApplyLoadedElement(DependencyObject element)
    {
        var baseline = GetBaseline(element);
        element.SetCurrentValue(TextElement.FontSizeProperty, baseline * _currentScale);
    }

    private static double GetBaseline(DependencyObject element)
    {
        if (Baselines.TryGetValue(element, out var existing)) return existing.Size;

        var size = (double)element.GetValue(TextElement.FontSizeProperty);
        var source = DependencyPropertyHelper.GetValueSource(element, TextElement.FontSizeProperty);
        if (source.BaseValueSource == BaseValueSource.Inherited && _currentScale > 0)
            size /= _currentScale;
        size = double.IsFinite(size) && size > 0 ? size : 13.0;
        Baselines.Add(element, new FontBaseline(size));
        return size;
    }

    private static IEnumerable<DependencyObject> Enumerate(DependencyObject root)
    {
        var pending = new Stack<DependencyObject>();
        var seen = new HashSet<DependencyObject>(ReferenceEqualityComparer.Instance);
        pending.Push(root);
        while (pending.Count > 0)
        {
            var current = pending.Pop();
            if (!seen.Add(current)) continue;
            yield return current;

            if (current is Visual or Visual3D)
            {
                var count = VisualTreeHelper.GetChildrenCount(current);
                for (var index = count - 1; index >= 0; index--)
                    pending.Push(VisualTreeHelper.GetChild(current, index));
            }

            if (current is FrameworkElement or FrameworkContentElement)
            {
                foreach (var child in LogicalTreeHelper.GetChildren(current).OfType<DependencyObject>())
                    pending.Push(child);
            }
        }
    }
}
