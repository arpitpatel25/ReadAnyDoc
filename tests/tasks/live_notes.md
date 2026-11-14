# Live smoke notes (Worker E)

Model: claude-haiku-5-5 through `claude -p`. Written by tests/tasks/live_smoke.py.

---

Run at 2026-11-11 18:14:41: classify

## classify the cached table page

### call 1 (4.2 s, usage {"input_tokens": 2, "output_tokens": 260, "cost_usd": 0.006461799999999999, "seconds": 4.2})

System prompt:

```
You classify a document. You are given categories, each with numbered criteria, and the text of the document's first pages.
For every criterion of every category decide whether the document meets it, judging only from the text given.
Reply with JSON only, in this shape (one entry per category, one boolean per criterion, in the given order):
{"categories": [{"category": "<name>", "criteria": [true, false]}], "best": "<name of the category that fits best>"}
```

User prompt:

```
Categories:
- category: "exhibitor directory"
    1. lists companies or organisations
    2. has booth numbers
    3. has contact names or websites
- category: "invoice"
    1. has an invoice number
    2. lists amounts due
- category: "research paper"
    1. has an abstract
    2. has citations or references

Pages:
=== Page 1 ===
# Exhibitor List
<table>
  <thead>
    <tr>
      <th>Booth #</th>
      <th>Company/Organization</th>
      <th>Contact</th>
      <th>Website</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td colspan="4"><strong>Atrium</strong></td>
    </tr>
    <tr>
      <td>100</td>
      <td>Angle Park, Inc.</td>
      <td>Martin Baumgaertner</td>
      <td>anglepark.com</td>
    </tr>
    <tr>
      <td>101</td>
      <td>Bluewater Studio</td>
      <td>Erich Zuern</td>
      <td>bluewater.studio</td>
    </tr>
    <tr>
      <td>102</td>
      <td>Curate One</td>
      <td>Dan Jecha</td>
      <td>curateone.com</td>
    </tr>
    <tr>
      <td>103</td>
      <td>Guardian Fine Art Services</td>
      <td>Christine Anderson</td>
      <td>guardianfineart.com</td>
    </tr>
    <tr>
      <td>104</td>
      <td>Mutual of America</td>
      <td>Paul Wierzba</td>
      <td>mutualofamerica.com</td>
    </tr>
    <tr>
      <td>105</td>
      <td>Tour-Mate Systems Ltd.</td>
      <td>Lynn Taylor</td>
      <td>tourmate.com</td>
    </tr>
    <tr>
      <td>106</td>
      <td>Taylor Studios, Inc.</td>
      <td>Danielle Rice</td>
      <td>taylorstudios.com</td>
    </tr>
    <tr>
      <td>107</td>
      <td>The Kubala Washatko Architects</td>
      <td>Wayne Reckard</td>
      <td>tkwa.com</td>
    </tr>
    <tr>
      <td>108</td>
      <td>Midway Spark</td>
      <td>Eli Ball</td>
      <td>midwayspark.com</td>
    </tr>
    <tr>
      <td>109</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
    <tr>
      <td>110</td>
      <td>Solid Light, Inc.</td>
      <td>Beth Geiser</td>
      <td>solidlight-inc.com</td>
    </tr>
    <tr>
      <td>111</td>
      <td>TimeLooper Inc.</td>
      <td>Andrew Feinberg</td>
      <td>timelooper.com</td>
    </tr>
    <tr>
      <td colspan="4"><strong>Milwaukee Foyer</strong></td>
    </tr>
    <tr>
      <td>200</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
    <tr>
      <td>201</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
    <tr>
      <td>202</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
    <tr>
      <td>203</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
  </tbody>
</table>

Return the JSON now.
```

Reply:

```
{"categories": [{"category": "exhibitor directory", "criteria": [true, true, true]}, {"category": "invoice", "criteria": [false, false]}, {"category": "research paper", "criteria": [false, false]}], "best": "exhibitor directory"}
```

### result

```json
{
 "response_type": "classify",
 "result": {
  "category": "exhibitor directory"
 },
 "response_confidence": {
  "categories": [
   {
    "category": "exhibitor directory",
    "confidence": 1.0,
    "criteria_confidence": [
     {
      "criterion": "lists companies or organisations",
      "confidence": "high"
     },
     {
      "criterion": "has booth numbers",
      "confidence": "high"
     },
     {
      "criterion": "has contact names or websites",
      "confidence": "high"
     }
    ]
   },
   {
    "category": "invoice",
    "confidence": 0.0,
    "criteria_confidence": [
     {
      "criterion": "has an invoice number",
      "confidence": "low"
     },
     {
      "criterion": "lists amounts due",
      "confidence": "low"
     }
    ]
   },
   {
    "category": "research paper",
    "confidence": 0.0,
    "criteria_confidence": [
     {
      "criterion": "has an abstract",
      "confidence": "low"
     },
     {
      "criterion": "has citations or references",
      "confidence": "low"
     }
    ]
   }
  ]
 },
 "usage": {
  "num_pages": 1,
  "num_categories": 3,
  "credits": null
 },
 "extra_metadata": {
  "grouping": "events"
 }
}
```

Calls in this run: 1; cost reported by the CLI: $0.0065

---

Run at 2026-11-11 18:14:49: extract split edit

## extract with citations on cached table page 26f221a3e03a0084c486be718476250c9247_pg2_pg1

### call 1 (7.9 s, usage {"input_tokens": 2, "output_tokens": 1655, "cost_usd": 0.005176330000000001, "seconds": 7.9})

System prompt:

```
Be precise and thorough.

You extract structured data from a parsed document.
- Use only what the document text says. Never invent values and never use outside knowledge.
- Return one JSON value that follows the JSON Schema exactly: include every declared property, use null when the document does not contain the value, never add properties that are not declared, and respect enum, pattern, minimum/maximum and array item rules.
- Copy values as written (names, numbers, identifiers, dates) unless the schema or the instructions ask for another format.
- For arrays, include every matching item found in the pages you are given, in document order. Do not stop early and do not summarise.
- Reply with the JSON only: no prose, no code fences.
```

User prompt:

```
Document:
=== Page 1 ===
[r1|Title] # Exhibitor List
[r2|Table] <table>
  <thead>
    <tr>
      <th>Booth #</th>
      <th>Company/Organization</th>
      <th>Contact</th>
      <th>Website</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td colspan="4"><strong>Atrium</strong></td>
    </tr>
    <tr>
      <td>100</td>
      <td>Angle Park, Inc.</td>
      <td>Martin Baumgaertner</td>
      <td>anglepark.com</td>
    </tr>
    <tr>
      <td>101</td>
      <td>Bluewater Studio</td>
      <td>Erich Zuern</td>
      <td>bluewater.studio</td>
    </tr>
    <tr>
      <td>102</td>
      <td>Curate One</td>
      <td>Dan Jecha</td>
      <td>curateone.com</td>
    </tr>
    <tr>
      <td>103</td>
      <td>Guardian Fine Art Services</td>
      <td>Christine Anderson</td>
      <td>guardianfineart.com</td>
    </tr>
    <tr>
      <td>104</td>
      <td>Mutual of America</td>
      <td>Paul Wierzba</td>
      <td>mutualofamerica.com</td>
    </tr>
    <tr>
      <td>105</td>
      <td>Tour-Mate Systems Ltd.</td>
      <td>Lynn Taylor</td>
      <td>tourmate.com</td>
    </tr>
    <tr>
      <td>106</td>
      <td>Taylor Studios, Inc.</td>
      <td>Danielle Rice</td>
      <td>taylorstudios.com</td>
    </tr>
    <tr>
      <td>107</td>
      <td>The Kubala Washatko Architects</td>
      <td>Wayne Reckard</td>
      <td>tkwa.com</td>
    </tr>
    <tr>
      <td>108</td>
      <td>Midway Spark</td>
      <td>Eli Ball</td>
      <td>midwayspark.com</td>
    </tr>
    <tr>
      <td>109</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
    <tr>
      <td>110</td>
      <td>Solid Light, Inc.</td>
      <td>Beth Geiser</td>
      <td>solidlight-inc.com</td>
    </tr>
    <tr>
      <td>111</td>
      <td>TimeLooper Inc.</td>
      <td>Andrew Feinberg</td>
      <td>timelooper.com</td>
    </tr>
    <tr>
      <td colspan="4"><strong>Milwaukee Foyer</strong></td>
    </tr>
    <tr>
      <td>200</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
    <tr>
      <td>201</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
    <tr>
      <td>202</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
    <tr>
      <td>203</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
  </tbody>
</table>

JSON Schema:
{"type": "object", "properties": {"title": {"type": "string"}, "atrium_exhibitors": {"type": "array", "description": "Exhibitors listed under the Atrium heading only", "items": {"type": "object", "properties": {"booth": {"type": "string", "pattern": "^\\d+$"}, "company": {"type": "string"}, "website": {"type": "string"}}}}, "event_date": {"type": "string", "description": "Date of the event, if printed on the page"}}}

Source labels: every block of the document starts with a label such as [r12|Table].
Write every leaf value (string, number, boolean or null, including each item of an array of scalars) as {"v": <value>, "r": ["r12"]}, where "r" lists the labels of the blocks the value was read from (usually exactly one). Use {"v": null, "r": []} when the value is missing. Objects and arrays keep their normal shape; only the leaves are wrapped.

Return the JSON now.
```

Reply:

```
{"title":{"v":"Exhibitor List","r":["r1"]},"atrium_exhibitors":[{"booth":{"v":"100","r":["r2"]},"company":{"v":"Angle Park, Inc.","r":["r2"]},"website":{"v":"anglepark.com","r":["r2"]}},{"booth":{"v":"101","r":["r2"]},"company":{"v":"Bluewater Studio","r":["r2"]},"website":{"v":"bluewater.studio","r":["r2"]}},{"booth":{"v":"102","r":["r2"]},"company":{"v":"Curate One","r":["r2"]},"website":{"v":"curateone.com","r":["r2"]}},{"booth":{"v":"103","r":["r2"]},"company":{"v":"Guardian Fine Art Services","r":["r2"]},"website":{"v":"guardianfineart.com","r":["r2"]}},{"booth":{"v":"104","r":["r2"]},"company":{"v":"Mutual of America","r":["r2"]},"website":{"v":"mutualofamerica.com","r":["r2"]}},{"booth":{"v":"105","r":["r2"]},"company":{"v":"Tour-Mate Systems Ltd.","r":["r2"]},"website":{"v":"tourmate.com","r":["r2"]}},{"booth":{"v":"106","r":["r2"]},"company":{"v":"Taylor Studios, Inc.","r":["r2"]},"website":{"v":"taylorstudios.com","r":["r2"]}},{"booth":{"v":"107","r":["r2"]},"company":{"v":"The Kubala Washatko Architects","r":["r2"]},"website":{"v":"tkwa.com","r":["r2"]}},{"booth":{"v":"108","r":["r2"]},"company":{"v":"Midway Spark","r":["r2"]},"website":{"v":"midwayspark.com","r":["r2"]}},{"booth":{"v":"109","r":["r2"]},"company":{"v":null,"r":[]},"website":{"v":null,"r":[]}},{"booth":{"v":"110","r":["r2"]},"company":{"v":"Solid Light, Inc.","r":["r2"]},"website":{"v":"solidlight-inc.com","r":["r2"]}},{"booth":{"v":"111","r":["r2"]},"company":{"v":"TimeLooper Inc.","r":["r2"]},"website":{"v":"timelooper.com","r":["r2"]}}],"event_date":{"v":null,"r":[]}}
```

### result

```json
{
 "response_type": "v3_extract",
 "result": {
  "title": {
   "value": "Exhibitor List",
   "citations": [
    {
     "type": "Title",
     "content": "# Exhibitor List",
     "bbox": {
      "left": 0.0457,
      "top": 0.051,
      "width": 0.2481,
      "height": 0.0519,
      "page": 1,
      "original_page": 1
     },
     "confidence": "high",
     "granular_confidence": {
      "extract_confidence": 0.95,
      "parse_confidence": 1.0
     },
     "parentBlock": {
      "type": "Title",
      "bbox": {
       "left": 0.044,
       "top": 0.0489,
       "width": 0.2533,
       "height": 0.0498,
       "page": 1,
       "original_page": 1
      },
      "content": "",
      "image_url": null,
      "chart_data": null,
      "confidence": "high",
      "granular_confidence": {
       "extract_confidence": null,
       "parse_confidence": 1.0
      }
     }
    }
   ]
  },
  "atrium_exhibitors": [
   {
    "booth": {
     "value": "100",
     "citations": [
      {
       "type": "Table",
       "content": "100",
       "bbox": {
        "left": 0.0906,
        "top": 0.2431,
        "width": 0.0232,
        "height": 0.0185,
        "page": 1,
        "original_page": 1
       },
       "confidence": "high",
       "granular_confidence": {
        "extract_confidence": 0.95,
        "parse_confidence": 1.0
       },
       "parentBlock": {
        "type": "Table",
        "bbox": {
         "left": 0.046,
         "top": 0.1618,
         "width": 0.908,
         "height": 0.6702,
         "page": 1,
         "original_page": 1
        },
        "content": "",
        "image_url": null,
        "chart_data": null,
        "confidence": "high",
        "granular_confidence": {
         "extract_confidence": null,
         "parse_confidence": 1.0
        }
       }
      }
     ]
    },
    "company": {
     "value": "Angle Park, Inc.",
     "citations": [
      {
       "type": "Table",
       "content": "Angle Park, Inc.",
       "bbox": {
        "left": 0.1325,
        "top": 0.2431,
        "width": 0.0979,
        "height": 0.0185,
        "page": 1,
        "original_page": 1
       },
       "confidence": "high",
       "granular_confidence": {
        "extract_confidence": 0.95,
        "parse_confidence": 1.0
       },
       "parentBlock": {
        "type": "Table",
        "bbox": {
         "left": 0.046,
         "top": 0.1618,
         "width": 0.908,
         "height": 0.6702,
         "page": 1,
         "original_page": 1
        },
        "content": "",
        "image_url": null,
        "chart_data": null,
        "confidence": "high",
        "granular_confidence": {
         "extract_confidence": null,
         "parse_confidence": 1.0
        }
       }
      }
     ]
    },
    "website": {
     "value": "anglepark.com",
     "citations": [
      {
       "type": "Table",
       "content": "anglepark.com",
       "bbox": {
        "left": 0.673,
        "top": 0.2431,
        "width": 0.0911,
        "height": 0.0185,
        "page": 1,
        "original_page": 1
       },
       "confidence": "high",
       "granular_confidence": {
        "extract_confidence": 0.95,
        "parse_confidence": 1.0
       },
       "parentBlock": {
        "type": "Table",
        "bbox": {
         "left": 0.046,
         "top": 0.1618,
         "width": 0.908,
         "height": 0.6702,
         "page": 1,
         "original_page": 1
        },
        "content": "",
        "image_url": null,
        "chart_data": null,
        "confidence": "high",
        "granular_confidence": {
         "extract_confidence": null,
         "parse_confidence": 1.0
        }
       }
      }
     ]
    }
   },
   {
    "booth": {
     "value": "101",
     "citations": [
      {
       "type": "Table",
       "content": "101",
       "bbox": {
        "left": 0.0906,
        "top": 0.2782,
        "width": 0.0232,
        "height": 0.0185,
        "page": 1,
        "original_page": 1
       },
       "confidence": "high",
       "granular_confidence": {
        "extract_confidence": 0.95,
        "parse_confidence": 1.0
       },
       "parentBlock": {
        "type": "Table",
        "bbox": {
         "left": 0.046,
         "top": 0.1618,
         "width": 0.908,
         "height": 0.6702,
         "page": 1,
         "original_page": 1
        },
        "content": "",
        "image_url": null,
        "chart_data": null,
        "confidence": "high",
        "granular_confidence": {
         "extract_confidence": null,
         "parse_confidence": 1.0
        }
       }
      }
     ]
    },
    "company": {
     "value": "Bluewater Studio",
     "citations": [
      {
       "type": "Table",
       "content": "Bluewater Studio",
       "bbox": {
        "left": 0.1325,
        "top": 0.2782,
        "width": 0.1049,
        "height": 0.0185,
        "page": 1,
        "original_page": 1
       },
       "confidence": "high",
       "granular_confidence": {
        "extract_confidence": 0.95,
        "parse_confidence": 1.0
       },
       "parentBlock": {
        "type": "Table",
        "bbox": {
         "left": 0.046,
         "top": 0.1618,
         "width": 0.908,
         "height": 0.6702,
         "page": 1,
         "original_page": 1
        },
        "content": "",
        "image_url": null,
        "chart_data": null,
        "confidence": "high",
        "granular_confidence": {
         "extract_confidence": null,
         "parse_confidence": 1.0
        }
       }
      }
     ]
    },
    "website": {
     "value": "bluewater.studio",
     "citations": [
      {
       "type": "Table",
       "content": "Bluewater Studio",
       "bbox": {
        "left": 0.1325,
        "top": 0.2782,
        "width": 0.1049,
        "height": 0.0185,
        "page": 1,
        "original_page": 1
       },
       "confidence": "high",
       "granular_confidence": {
        "extract_confidence": 0.95,
        "parse_confidence": 1.0
       },
       "parentBlock": {
        "type
```

## split of a 5-page synthetic statement packet

### call 1 (5.5 s, usage {"input_tokens": 2, "output_tokens": 577, "cost_usd": 0.0044179300000000005, "seconds": 5.5})

System prompt:

```
You split a document into sections. You are given the section definitions, the splitting rules and the text of the pages.
Decide for every section which pages belong to it. Page numbers are the ones in the "=== Page N ===" markers.
- Use only the section names given. A section that does not occur gets an empty page list.
- A page that fits no section is left out.
- "conf" is "high" when the page text clearly supports the assignment and "low" when you are unsure.
- For a section with a partition key, also group its pages by the value of that key as it appears in the text (for example one partition per account number). A partition name is the key value exactly as written.
Reply with JSON only, in this shape:
{"sections": [{"name": "<section name>", "pages": [1, 2], "conf": "high", "partitions": [{"name": "<key value>", "pages": [1], "conf": "high"}]}]}
```

User prompt:

```
Sections:
- name: "Cover"; description: cover letter
- name: "Statements"; description: account statements; partition key: account number
- name: "Terms"; description: terms and conditions

Rules:
Split the document into the applicable sections. Sections may only overlap at their first and last page if at all.

Pages:
=== Page 1 ===
Cover letter
Dear customer, please find your statements enclosed.
=== Page 2 ===
Statement for Account 1111
<table><tr><td>row 0</td><td>Account 7700</td></tr><tr><td>row 1</td><td>Account 7701</td></tr><tr><td>row 2</td><td>Account 7702</td></tr><tr><td>row 3</td><td>Account 7703</td></tr></table> [table truncated: 26 more rows]
=== Page 3 ===
Statement for Account 1111 (continued)
=== Page 4 ===
Statement for Account 2222
=== Page 5 ===
Terms and conditions
These terms apply to all accounts.

Return the JSON now.
```

Reply:

```
{"sections": [{"name": "Cover", "pages": [1], "conf": "high", "partitions": []}, {"name": "Statements", "pages": [2, 3, 4], "conf": "high", "partitions": [{"name": "1111", "pages": [2, 3], "conf": "high"}, {"name": "2222", "pages": [4], "conf": "high"}]}, {"name": "Terms", "pages": [5], "conf": "high", "partitions": []}]}
```

### result

```json
{
 "response_type": "split",
 "result": {
  "section_mapping": {
   "Cover": [
    1
   ],
   "Statements": [
    2,
    3,
    4
   ],
   "Terms": [
    5
   ]
  },
  "splits": [
   {
    "name": "Cover",
    "pages": [
     1
    ],
    "conf": "high",
    "partitions": null
   },
   {
    "name": "Statements",
    "pages": [
     2,
     3,
     4
    ],
    "conf": "high",
    "partitions": [
     {
      "name": "1111",
      "pages": [
       2,
       3
      ],
      "conf": "high"
     },
     {
      "name": "2222",
      "pages": [
       4
      ],
      "conf": "high"
     }
    ]
   },
   {
    "name": "Terms",
    "pages": [
     5
    ],
    "conf": "high",
    "partitions": null
   }
  ]
 },
 "usage": {
  "num_pages": 5,
  "credits": null
 }
}
```

## edit of the generated fillable PDF

### call 1 (5.1 s, usage {"input_tokens": 2, "output_tokens": 377, "cost_usd": 0.00428553, "seconds": 5.1})

System prompt:

```
You fill in a form. You are given the instructions from the user and the list of the form's fields (id, type, description, options, current value).
Decide which fields the instructions give a value for, and that value.
- text: the string to write, formatted the way the field's description asks for when it says so.
- checkbox: true to tick, false to untick.
- radio and dropdown: exactly one of the listed options.
- Leave out every field the instructions say nothing about. Never invent a value.
Reply with JSON only, in this shape: {"fields": [{"id": 0, "value": "..."}]}
```

User prompt:

```
Instructions:
The applicant is Priya Sharma, born 12 April 1990. She agrees to the terms, wants the premium plan and lives in Germany. Leave comments empty.

Fields:
{"id": 0, "type": "text", "description": "Full name (field: full_name)"}
{"id": 1, "type": "text", "description": "Date of birth (MM/DD/YYYY) (field: dob)"}
{"id": 2, "type": "text", "description": "Comments"}
{"id": 3, "type": "checkbox", "description": "I agree to the terms (field: agree)"}
{"id": 4, "type": "radio", "description": "plan", "options": ["Basic", "Premium"]}
{"id": 5, "type": "dropdown", "description": "Country", "options": ["India", "Germany", "United States"]}

Return the JSON now.
```

Reply:

```
{"fields": [{"id": 0, "value": "Priya Sharma"}, {"id": 1, "value": "04/12/1990"}, {"id": 2, "value": ""}, {"id": 3, "value": "true"}, {"id": 4, "value": "Premium"}, {"id": 5, "value": "Germany"}]}
```

### result

```json
{
 "filled_fields": {
  "full_name": [
   "Priya Sharma",
   []
  ],
  "dob": [
   "04/12/1990",
   []
  ],
  "comments": [
   "",
   []
  ],
  "agree": [
   "/Yes",
   [
    "/Yes"
   ]
  ],
  "plan": [
   "/Premium",
   [
    "/Off",
    "/Premium"
   ]
  ],
  "country": [
   "Germany",
   []
  ]
 },
 "response": {
  "response_type": "edit",
  "document_url": "/var/folders/7s/mkl254nn37783802j97kd1d40000gn/T/readanydoc_live_911gy26f/filled.pdf",
  "form_schema": [
   {
    "bbox": {
     "left": 0.2614,
     "top": 0.0934,
     "width": 0.3922,
     "height": 0.0227,
     "page": 1,
     "original_page": 1
    },
    "description": "Full name (field: full_name)",
    "type": "text",
    "fill": true,
    "value": null,
    "font_size": null
   },
   {
    "bbox": {
     "left": 0.2614,
     "top": 0.1439,
     "width": 0.2288,
     "height": 0.0227,
     "page": 1,
     "original_page": 1
    },
    "description": "Date of birth (MM/DD/YYYY) (field: dob)",
    "type": "text",
    "fill": true,
    "value": null,
    "font_size": null
   },
   {
    "bbox": {
     "left": 0.2614,
     "top": 0.1919,
     "width": 0.5556,
     "height": 0.0758,
     "page": 1,
     "original_page": 1
    },
    "description": "Comments",
    "type": "text",
    "fill": true,
    "value": null,
    "font_size": null
   },
   {
    "bbox": {
     "left": 0.1307,
     "top": 0.3005,
     "width": 0.0196,
     "height": 0.0152,
     "page": 1,
     "original_page": 1
    },
    "description": "I agree to the terms (field: agree)",
    "type": "checkbox",
    "fill": true,
    "value": null,
    "font_size": null
   },
   {
    "bbox": {
     "left": 0.2369,
     "top": 0.351,
     "width": 0.0196,
     "height": 0.0152,
     "page": 1,
     "original_page": 1
    },
    "description": "Basic (field: plan)",
    "type": "radio",
    "fill": true,
    "value": null,
    "font_size": null
   },
   {
    "bbox": {
     "left": 0.4003,
     "top": 0.351,
     "width": 0.0196,
     "height": 0.0152,
     "page": 1,
     "original_page": 1
    },
    "description": "Premium (field: plan)",
    "type": "radio",
    "fill": true,
    "value": null,
    "font_size": null
   },
   {
    "bbox": {
     "left": 0.2614,
     "top": 0.3965,
     "width": 0.2288,
     "height": 0.0227,
     "page": 1,
     "original_page": 1
    },
    "description": "Country",
    "type": "dropdown",
    "fill": true,
    "value": null,
    "font_size": null
   }
  ],
  "usage": {
   "num_pages": 1,
   "credits": null
  }
 }
}
```

Calls in this run: 3; cost reported by the CLI: $0.0139
