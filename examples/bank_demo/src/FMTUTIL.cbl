       IDENTIFICATION DIVISION.
       PROGRAM-ID. FMTUTIL.
      *================================================================*
      * FMTUTIL - REPORT FIELD FORMATTING                              *
      * STYLE D BUILDS THE DATE WITH STRING AND DOES NOT CLEAR FM-OUT  *
      * FIRST (HISTORICAL BEHAVIOUR - CALLERS RELY ON IT).             *
      *================================================================*
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-ED-CR                PIC ZZZ,ZZZ,ZZ9.99CR.
       01  WS-ED-MINUS             PIC --,---,---,--9.99.
       01  WS-ED-PCT               PIC ZZ9.999.
       01  WS-PCT                  PIC 9(3)V9(3).
       01  WS-RIGHT                PIC X(20) JUSTIFIED RIGHT.
       01  WS-DATE-X               PIC X(8).
       LINKAGE SECTION.
       COPY FMTPARM.
       PROCEDURE DIVISION USING FMT-PARMS.
       0000-MAIN.
           EVALUATE FM-STYLE
               WHEN 'A'
                   MOVE FM-AMOUNT TO WS-ED-CR
                   MOVE WS-ED-CR TO FM-OUT
               WHEN 'B'
                   MOVE FM-AMOUNT TO WS-ED-MINUS
                   MOVE WS-ED-MINUS TO FM-OUT
               WHEN 'R'
                   MOVE FM-AMOUNT TO WS-ED-CR
                   MOVE WS-ED-CR TO WS-RIGHT
                   MOVE WS-RIGHT TO FM-OUT
               WHEN 'D'
                   MOVE FM-DATE TO WS-DATE-X
                   STRING WS-DATE-X(5:2) '/' WS-DATE-X(7:2) '/'
                          WS-DATE-X(1:4) DELIMITED BY SIZE
                          INTO FM-OUT
                   END-STRING
               WHEN 'P'
                   COMPUTE WS-PCT = FM-RATE * 100
                   MOVE WS-PCT TO WS-ED-PCT
                   MOVE SPACES TO FM-OUT
                   STRING WS-ED-PCT DELIMITED BY SIZE
                          '%' DELIMITED BY SIZE
                          INTO FM-OUT
                   END-STRING
               WHEN OTHER
                   MOVE ALL '*' TO FM-OUT
           END-EVALUATE
           GOBACK.
