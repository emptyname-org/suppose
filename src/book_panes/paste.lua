-- Pandoc filter for formatted text pasted as Markdown: what Markdown has no
-- word for goes, what a style says Markdown says.

local function says(el, pattern)
  local style = el.attributes.style
  return style ~= nil and style:lower():match(pattern) ~= nil
end

local function unwrap(el)
  return el.content
end

-- a paragraph of line breaks alone is a gap, and Markdown has those already
local function gap(el)
  for _, inline in ipairs(el.content) do
    if inline.t ~= "LineBreak" and inline.t ~= "SoftBreak" and inline.t ~= "Space" then
      return nil
    end
  end
  return {}
end

-- a Markdown table has one line of text to a cell, and no cell over two
local function plain_table(el)
  local simple = pandoc.utils.to_simple_table(el)
  local function plain(cells)
    for index, blocks in ipairs(cells) do
      blocks = pandoc.Blocks(blocks):walk({
        CodeBlock = function(code)
          return pandoc.Plain({ pandoc.Code((code.text:gsub("%s*\n%s*", " "))) })
        end,
      })
      local line = pandoc.utils.blocks_to_inlines(blocks, { pandoc.Space() })
      cells[index] = { pandoc.Plain(line:walk({ LineBreak = pandoc.Space, SoftBreak = pandoc.Space })) }
    end
  end
  plain(simple.headers)
  for _, row in ipairs(simple.rows) do
    plain(row)
  end
  for index in ipairs(simple.widths) do
    simple.widths[index] = 0
  end
  return pandoc.utils.from_simple_table(simple)
end

-- a link is to somewhere, not a program
local function unsafe(target)
  local bare = target:lower():gsub("%%%x%x", ""):gsub("[%s%c]", "")
  local scheme = bare:match("^(%a[%w+.-]*):")
  return scheme == "javascript" or scheme == "vbscript" or scheme == "data"
end

local styled = {
  -- megabytes of base64 are no text
  Image = function(el)
    if el.src:match("^data:") then
      return el.caption
    end
  end,
  Span = function(el)
    local content = el.content
    if pandoc.utils.stringify(content):match("%S") then
      if says(el, "font%-style:%s*italic") then
        content = { pandoc.Emph(content) }
      end
      if says(el, "font%-weight:%s*bold") or says(el, "font%-weight:%s*[6-9]00") then
        content = { pandoc.Strong(content) }
      end
    end
    return content
  end,
  Div = unwrap,
  Para = gap,
  Plain = gap,
  Table = plain_table,
  Link = function(el)
    if unsafe(el.target) then
      return el.content
    end
  end,
  Header = function(el)
    if #el.content == 0 then
      return {}
    end
  end,
}

-- after the tables, whose cells take no blocks
local fenced = {
  -- fenced: indented code is none right behind a paragraph or in a list
  CodeBlock = function(el)
    if el.text == "" then
      return {}
    end
    if #el.classes == 0 then
      local longest = 2
      for run in el.text:gmatch("`+") do
        longest = math.max(longest, #run)
      end
      local fence = string.rep("`", longest + 1)
      local text = el.text:gsub("\n+$", "")
      return pandoc.RawBlock("markdown", fence .. "\n" .. text .. "\n" .. fence)
    end
  end,
}

-- bold in bold is bold
local flat = {
  Strong = function(el)
    return pandoc.Strong(el.content:walk({ Strong = unwrap }))
  end,
  Emph = function(el)
    return pandoc.Emph(el.content:walk({ Emph = unwrap }))
  end,
}

return { styled, fenced, flat }
