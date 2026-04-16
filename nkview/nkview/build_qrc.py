import os

icons_dir = r'c:\Users\herro\repos\nuke_parser\nkview\nkview\icons'
qrc_path = r'c:\Users\herro\repos\nuke_parser\nkview\nkview\qresource.qrc'

with open(qrc_path, 'w') as f:
    f.write('<!DOCTYPE RCC><RCC version=\"1.0\">\n<qresource>\n')
    
    # Root icons
    try:
        for file in os.listdir(icons_dir):
            if file.endswith('.png'):
                f.write(f'    <file alias=\"{file}\">icons/{file}</file>\n')
    except Exception as e:
        print('Error root:', e)

    # Nuke types icons
    nuke_types_dir = os.path.join(icons_dir, 'nuke_types')
    try:
        if os.path.exists(nuke_types_dir):
            for file in os.listdir(nuke_types_dir):
                if file.endswith('.png'):
                    f.write(f'    <file alias=\"nuke_types/{file}\">icons/nuke_types/{file}</file>\n')
    except Exception as e:
        print('Error types:', e)

    f.write('</qresource>\n</RCC>\n')

print('Done')
