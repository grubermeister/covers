# WorldCovers | Vision

## Background

Postal marking catalogs -- references documenting town markings (including cancellations), rate marks, and auxiliary/instructional markings found in manuscript or via handstamp on mail covers -- are largely preserved in specialized print publications maintained by individual experts or small volunteer groups. These catalogs are often out of print, use inconsistent and unclear formats, and accessible only within narrow expert circles. This fragmentation creates barriers to research, collaboration, and knowledge preservation.

The *American Postal Markings Catalog* (APMC) is the first dataset to be managed by this system. The APMC currently consists of the digitized *American Stampless Cover Catalog* (ASCC) and will soon incorporate the *Virginia Postal History Catalog* (VPHC). A prior implementation of the ASCC digitization effort exists as a legacy web application at worldcovers.org, developed under the sponsorship of the *US Philatelic Classics Society* (USPCS). It was built without formal design work and is deemed to have reached the limits of its architecture. Implemented in Adobe ColdFusion, the shrinking talent pool and hosting constraints of the software make ongoing maintenance impractical, and the system cannot reasonably be extended to meet the goals that society members have articulated (see Goals below). This document describes a ground-up re-implementation (nicknamed *WoCo*) that replaces and extends the capabilities of that original application.

## Purpose

*WorldCovers* is a free, open-source software system for cataloging, researching, and collaborating on global postal marking and cover data. It provides a unified, searchable platform that replaces static print catalogs with a living, community-maintained database. Submissions are reviewed by subject-matter experts to maintain the scholarly standards the philatelic community expects.

## Why Custom Software?

Alternatives to custom development were evaluated before this project began.

**Wiki platforms.** MediaWiki proved too cumbersome to deploy and maintain for a small volunteer organization. Other wiki packages lacked the structured data, search, and review workflow capabilities the project requires.

**WordPress extension.** Because the USPCS website runs on WordPress, a plugin-based approach was considered. This was rejected due to the cumulative maintenance cost: scarce competent PHP developers, the burden of tracking WordPress core updates, risk of destabilizing the main USPCS site (or the cost of running a separate instance), and the large surface area of unused WordPress features creating bloat and potential security exposure.

Custom development allows the system to be purpose-built for structured catalog data with expert review workflows, without inheriting the maintenance liabilities of a legacy or general-purpose platform.

## Goals

1. Digitized access -- Provide free, searchable access to postal marking and cover records currently locked in print catalogs, starting with the APMC under USPCS sponsorship.
2. Community contribution with expert curation -- Enable collectors and researchers to submit new records while preserving data quality through expert review workflows.
3. Multi-catalog extensibility -- Support multiple catalogs (by region, era, or specialty) within a single platform, with independent management of each.
4. Archival interoperability -- Structure and expose data in forms useful to professional archival systems, without attempting to be an archival-grade preservation system itself.
5. Comprehensive audit trails -- Maintain version history and change tracking sufficient for scholarly accountability and data recovery.
6. Feature parity with worldcovers.org -- Re-implement the capabilities of the existing ColdFusion application as a baseline for the new system.

## Non-Goals

* Not a marketplace -- WoCo will not handle payment transactions or facilitate buying and selling.
* Not a social network -- WoCo is a collaborative reference platform, not a forum. Its contribution model is closer to a wiki than to social media.
* Not an archival-grade preservation system -- WoCo supports interoperability with such systems but does not itself implement professional archival standards.
* Not a stamp catalog -- The scope is covers and postal markings. Stamp-specific cataloging (designs, issues, values) is out of scope, though stamps appearing on covers may be incidentally recorded.

## Success Criteria

- [ ] All features provided by the current worldcovers.org application are re-implemented in WoCo.
- [ ] APMC data (ASCC and VPHC) is searchable and browsable through the new system.
- [ ] Beta testers from the USPCS community confirm their workflows are matched or improved by the new interface.
