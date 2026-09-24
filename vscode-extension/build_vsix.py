"""Package this dependency-free local extension without fetching build tools."""
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parent
package = json.loads((root / 'package.json').read_text())
output = root / (package['name'] + '-' + package['version'] + '.vsix')
manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
<Metadata><Identity Language="en-US" Id="{package['name']}" Version="{package['version']}" Publisher="{package['publisher']}"/>
<DisplayName>Account Switchboard</DisplayName><Description xml:space="preserve">Local account-aware workspace launcher</Description>
<Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.85.0"/>
<Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="ui"/></Properties></Metadata>
<Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation><Dependencies/>
<Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/></Assets>
</PackageManifest>'''
types = '''<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="json" ContentType="application/json"/><Default Extension="js" ContentType="application/javascript"/>
<Default Extension="vsixmanifest" ContentType="text/xml"/></Types>'''
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('extension.vsixmanifest', manifest)
    archive.writestr('[Content_Types].xml', types)
    for name in ('package.json', 'extension.js', 'launcher.js'):
        archive.write(root / name, 'extension/' + name)
print(output)
