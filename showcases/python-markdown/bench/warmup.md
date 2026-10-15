---
title: Admonition Extension
---

Admonition
==========

Summary
-------

The Admonition extension adds [rST-style][rST] admonitions to Markdown documents.

This extension is included in the standard Markdown library.

[rST]: http://docutils.sourceforge.net/docs/ref/rst/directives.html#specific-admonitions

!!! info "Note"

    This extension is in __maintenance mode__.  We will continue to fix bugs
    and keep it up-to-date with the core parser, but no new features or
    changes in behavior will be made. If you need a feature that this
    extension does not offer, then you have three options (1) find an
    existing [third-party extension] which meets your needs, (2) [build your
    own extension], or (3) fork this extension (pursuant to its licensing
    requirements) and maintain it as a third-party extension.

    We recommend [PyMdown Admonition] as an actively developed alternative. 

[third-party extension]: index.md#third-party-extensions
[build your own extension]: api.md
[PyMdown Admonition]: https://facelessuser.github.io/pymdown-extensions/extensions/blocks/plugins/admonition/

Syntax
------

Admonitions are created using the following syntax:

``` md-render
---
extensions: [admonition]
---
!!! type "optional explicit title within double quotes"
    Any number of other indented markdown elements.

    This is the second paragraph.
```

`type` will be used as the CSS class name and as default title. It must be a
single word.

``` md-render
---
extensions: [admonition]
---
!!! note
    You should note that the title will be automatically capitalized.
```

Optionally, you can use custom titles.

``` md-render
---
extensions: [admonition]
---
!!! danger "Don't try this at home"
    ...
```

If you don't want a title, use a blank string `""`.

``` md-render
---
extensions: [admonition]
---
!!! important ""
    This is an admonition box without a title.
```

You can also provide additional CSS class names separated by spaces. The first
class should be the "type."

``` md-render
---
extensions: [admonition]
---
!!! danger highlight blink "Don't try this at home"
    ...
```

rST suggests the following "types": `attention`, `caution`, `danger`, `error`,
`hint`, `important`, `note`, `tip`, and `warning`; however, you're free to use
whatever you want.

Styling
-------

There is no CSS included as part of this extension. Check out the default
[Sphinx][sphinx] theme for inspiration.

[sphinx]: https://www.sphinx-doc.org/en/master/

## Usage

See [Extensions](index.md) for general extension usage. Use `admonition` as the
name of the extension.

This extension does not accept any special configuration options.

A trivial example:

```python
markdown.markdown(some_text, extensions=['admonition'])
```
---
title: Tables Extension
---

Tables
======

Summary
-------

The Tables extension adds the ability to create tables in Markdown documents.

This extension is included in the standard Markdown library.

!!! info "Note"

    This extension is in __maintenance mode__.  We will continue to fix bugs
    and keep it up-to-date with the core parser, but no new features or
    changes in behavior will be made. If you need a feature that this
    extension does not offer, then you have three options (1) find an
    existing [third-party extension] which meets your needs, (2) [build your
    own extension], or (3) fork this extension (pursuant to its licensing
    requirements) and maintain it as a third-party extension.

[third-party extension]: index.md#third-party-extensions
[build your own extension]: api.md

Syntax
------

Tables are defined using the syntax established in [PHP Markdown Extra][php].

[php]: http://www.michelf.com/projects/php-markdown/extra/#table

Consider the following text (taken from the above referenced PHP documentation).

``` md-render
---
extensions: [tables]
---
First Header  | Second Header
------------- | -------------
Content Cell  | Content Cell
Content Cell  | Content Cell
```

!!! seealso "See Also"

    The [Attribute Lists](./attr_list.md) extension includes support for defining attributes on table cells.

Usage
-----

See [Extensions](index.md) for general extension usage. Use `tables` as the
name of the extension.

See the [Library Reference](../library.md#extensions) for information about
configuring extensions.

The following options are provided to change the default behavior:

* **`use_align_attribute`**: Set to `True` to use `align` instead of an appropriate `style` attribute

    Default: `'False'`


A trivial example:

```python
markdown.markdown(some_text, extensions=['tables'])
```

### Examples

For an example, let us suppose that alignment should be controlled by the legacy `align`
attribute.

```pycon
>>> from markdown.extensions.tables import TableExtension
>>> html = markdown.markdown(text,
...                 extensions=[TableExtension(use_align_attribute=True)]
... )
```
---
title: Definition Lists Extension
---

Definition Lists
================

Summary
-------

The Definition Lists extension adds the ability to create definition lists in
Markdown documents.

This extension is included in the standard Markdown library.

!!! info "Note"

    This extension is in __maintenance mode__.  We will continue to fix bugs
    and keep it up-to-date with the core parser, but no new features or
    changes in behavior will be made. If you need a feature that this
    extension does not offer, then you have three options (1) find an
    existing [third-party extension] which meets your needs, (2) [build your
    own extension], or (3) fork this extension (pursuant to its licensing
    requirements) and maintain it as a third-party extension.

    We recommend [PyMdown Definition] as an actively developed alternative. 

[third-party extension]: index.md#third-party-extensions
[build your own extension]: api.md
[PyMdown Definition]: https://facelessuser.github.io/pymdown-extensions/extensions/blocks/plugins/definition/

Syntax
------

Definition lists are defined using the syntax established in
[PHP Markdown Extra][php].

[php]: http://www.michelf.com/projects/php-markdown/extra/#def-list

Consider the following text taken from the above referenced PHP documentation.

``` md-render
---
extensions: [def_list]
---
Apple
:   Pomaceous fruit of plants of the genus Malus in
    the family Rosaceae.

Orange
:   The fruit of an evergreen tree of the genus Citrus.
```

Usage
-----

See [Extensions](index.md) for general extension usage. Use `def_list` as the
name of the extension.

This extension does not accept any special configuration options.

A trivial example:

```python
markdown.markdown(some_text, extensions=['def_list'])
```
---
title: Abbreviations Extension
---

Abbreviations
=============

Summary
-------

The Abbreviations extension adds the ability to define abbreviations.
Specifically, any defined abbreviation is wrapped in  an `<abbr>` tag.

The Abbreviations extension is included in the standard Markdown library.

!!! info "Note"

    This extension is in __maintenance mode__.  We will continue to fix bugs
    and keep it up-to-date with the core parser, but no new features or
    changes in behavior will be made. If you need a feature that this
    extension does not offer, then you have three options (1) find an
    existing [third-party extension] which meets your needs, (2) [build your
    own extension], or (3) fork this extension (pursuant to its licensing
    requirements) and maintain it as a third-party extension.

[third-party extension]: index.md#third-party-extensions
[build your own extension]: api.md

Syntax
------

Abbreviations are defined using the syntax established in
[PHP Markdown Extra][php].

[php]: http://www.michelf.com/projects/php-markdown/extra/#abbr

For example, consider the following text (taken from the above referenced PHP documentation).

``` md-render
---
extensions: [abbr]
---
The HTML specification
is maintained by the W3C.

*[HTML]: Hyper Text Markup Language
*[W3C]:  World Wide Web Consortium
```

The backslash (`\`) is not permitted in an abbreviation. Any abbreviation
definitions which include one or more backslashes between the square brackets
will not be recognized as an abbreviation definition.

Usage
-----

See [Extensions](index.md) for general extension usage. Use `abbr` as the name
of the extension.

The following options are provided to configure the output:

* **`glossary`**:
    A dictionary where the `key` is the abbreviation and the `value` is the definition.

A trivial example:

```python
markdown.markdown(some_text, extensions=['abbr'])
```

Disabling Abbreviations
-----------------------

When using the `glossary` option, there may be times when you need to turn off
a specific abbreviation. To do this, set the abbreviation to `''` or `""`.

``` md-render
---
extensions: [abbr]
extension_configs:
    abbr:
        glossary:
            HTML: Hyper Text Markup Language
---
The HTML abbreviation is disabled on this page.

*[HTML]: ''
```---
title: Sane Lists Extension
---

Sane Lists
==========

Summary
-------

The Sane Lists extension alters the behavior of the Markdown List syntax
to be less surprising.

This extension is included in the standard Markdown library.

!!! info "Note"

    This extension is in __maintenance mode__.  We will continue to fix bugs
    and keep it up-to-date with the core parser, but no new features or
    changes in behavior will be made. If you need a feature that this
    extension does not offer, then you have three options (1) find an
    existing [third-party extension] which meets your needs, (2) [build your
    own extension], or (3) fork this extension (pursuant to its licensing
    requirements) and maintain it as a third-party extension.

[third-party extension]: index.md#third-party-extensions
[build your own extension]: api.md

Syntax
------

Sane Lists do not allow the mixing of list types. In other words, an ordered
list will not continue when an unordered list item is encountered and
vice versa. For example:

``` md-render
---
extensions: [sane_lists]
---
1. Ordered item 1
2. Ordered item 2

* Unordered item 1
* Unordered item 2
```

Whereas the default Markdown behavior would be to generate an unordered list.

Note that, unlike the default Markdown behavior, if a blank line is not
included between list items, the different list type is ignored completely.
This corresponds to the behavior of paragraphs. For example:

```md-render
---
extensions: [sane_lists]
---
A Paragraph.
* Not a list item.

1. Ordered list item.
* Not a separate list item.
```

Sane lists also recognize the number used in ordered lists. Given the following
list:

```md-render
---
extensions: [sane_lists]
---
4. Apples
5. Oranges
6. Pears
```

By default markdown will ignore the fact that the first line started
with item number "4" and the HTML list will start with a number "1".

In all other ways, Sane Lists should behave as normal Markdown lists.

Usage
-----

See [Extensions](index.md) for general extension usage. Use `sane_lists` as the
name of the extension.

This extension does not accept any special configuration options.

A trivial example:

```python
markdown.markdown(some_text, extensions=['sane_lists'])
```
